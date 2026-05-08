"""
Tool loop for MiniClaw.

Owns the Anthropic request cycle, tool execution, and response extraction for
one user message.
"""

import json
import logging
import re

import anthropic

from core import profiling

_REMEMBER_RE = re.compile(
    r"\n?##\s*remember:\n+topic:\s*(.+?)\n+content:\s*(.+?)(?=\n##|\Z)",
    re.IGNORECASE | re.DOTALL,
)

CHECKPOINT_INTERVAL = 15

CHECKPOINT_NUDGE = (
    "[CHECKPOINT — {n} tool calls in this turn]\n"
    "Step back briefly: in the calls so far, did any skill route on a phrasing\n"
    "that isn't in its SKILL.md? Did you correct a misroute? If so, call\n"
    "update_skill_hints now before continuing the user's request."
)

logger = logging.getLogger(__name__)


class ToolLoop:
    """Execute the Claude tool-use loop for a single user message."""

    def __init__(
        self,
        client,
        model: str,
        skill_loader,
        container_manager,
        conversation_state,
        memory_provider=None,
        max_rounds: int = 10,
    ):
        self.client = client
        self.model = model
        self.skill_loader = skill_loader
        self.container_manager = container_manager
        self.conversation_state = conversation_state
        self.memory_provider = memory_provider
        self.max_rounds = max_rounds

    def run(
        self,
        user_message: str,
        system_prompt: str,
        archive_callback=None,
        on_chunk=None,
    ) -> str:
        """
        Process a user message through Claude with tool support.

        archive_callback: optional Callable[[str, list[dict], str], None].
        Called once per completed turn with (user_message, tool_activity,
        response_text). tool_activity is a list of {"name", "input", "result"}
        dicts, one per tool call this turn (in order). Fires before prune.

        on_chunk: optional Callable[[str], None]. When provided, the Anthropic
        request runs in streaming mode and text deltas are forwarded to this
        callback as they arrive. Tool-use rounds still complete fully before
        tools execute; the streaming only affects WHEN text reaches the
        caller, not the round structure. When None, behaviour is identical
        to the non-streaming path.
        """
        if hasattr(self.container_manager, "start_turn"):
            self.container_manager.start_turn()
        self.conversation_state.append_user_text(user_message)
        effective_system_prompt = self._augment_system_prompt(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        tool_definitions = self.skill_loader.get_tool_definitions()
        tool_activity: list[dict] = []
        rounds = 0
        last_nudged_at = 0

        while rounds < self.max_rounds:
            rounds += 1

            # Build per-round system prompt — checkpoint nudge if we just
            # crossed a multiple of CHECKPOINT_INTERVAL since last nudge.
            tool_count = len(tool_activity)
            current_checkpoint = (tool_count // CHECKPOINT_INTERVAL) * CHECKPOINT_INTERVAL
            if (
                current_checkpoint > last_nudged_at
                and current_checkpoint > 0
                and self._any_opted_in_skill()
            ):
                round_system = (
                    effective_system_prompt
                    + "\n\n"
                    + CHECKPOINT_NUDGE.format(n=current_checkpoint)
                )
                last_nudged_at = current_checkpoint
            else:
                round_system = effective_system_prompt

            with profiling.stage("llm_claude"):
                if on_chunk is None:
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=4096,
                        system=round_system,
                        messages=self.conversation_state.select_messages_for_prompt(),
                        tools=tool_definitions if tool_definitions else anthropic.NOT_GIVEN,
                    )
                else:
                    # Stream text deltas to on_chunk as they arrive; the final
                    # message (including any tool_use blocks) is reconstructed
                    # via get_final_message so the rest of the loop is unchanged.
                    with self.client.messages.stream(
                        model=self.model,
                        max_tokens=4096,
                        system=round_system,
                        messages=self.conversation_state.select_messages_for_prompt(),
                        tools=tool_definitions if tool_definitions else anthropic.NOT_GIVEN,
                    ) as stream:
                        for delta in stream.text_stream:
                            try:
                                on_chunk(delta)
                            except Exception:
                                logger.exception("on_chunk callback raised; continuing")
                        response = stream.get_final_message()
                        # Streamed text blocks carry an SDK-internal `parsed_output`
                        # field that the Anthropic API rejects on echo with a 400.
                        # Replace each content block with a clean dict copy that
                        # excludes the helper fields.
                        response.content = [
                            self._sanitize_block(block) for block in response.content
                        ]

            if response.stop_reason == "tool_use":
                tool_results = self._handle_tool_calls(response, tool_activity)
                self.conversation_state.append_assistant_content(response.content)
                self.conversation_state.append_tool_results(tool_results)
                continue

            response_text = self._extract_text(response)
            self.conversation_state.append_assistant_content(response.content)

            logger.info(
                "Response ready: %d rounds, %d input / %d output tokens",
                rounds,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            if archive_callback is not None:
                try:
                    archive_callback(user_message, tool_activity, response_text)
                except Exception:
                    logger.exception("archive_callback failed")
            self.conversation_state.prune()
            return response_text

        logger.warning("Max tool rounds reached (%d)", self.max_rounds)
        if archive_callback is not None:
            try:
                archive_callback(user_message, tool_activity, "")
            except Exception:
                logger.exception("archive_callback failed")
        self.conversation_state.prune()
        return "I ran into an issue processing that request. Could you try again?"

    def _any_opted_in_skill(self) -> bool:
        for s in self.skill_loader.skills.values():
            fm = getattr(s, "frontmatter", None) or {}
            allow = (
                fm.get("metadata", {}).get("miniclaw", {})
                  .get("self_update", {}).get("allow_body")
            )
            if allow is True:
                return True
        return False

    def _augment_system_prompt(self, system_prompt: str, user_message: str) -> str:
        """Attach live memory recall relevant to the current user message."""
        if not self.memory_provider:
            return system_prompt

        recalled = self.memory_provider.recall_for_message(user_message)
        if not recalled:
            return system_prompt

        return (
            f"{system_prompt}\n"
            "\n--- Relevant Memory Recall ---\n"
            "Use this as supporting memory for the current turn. Verify details against it "
            "before making claims about prior preferences, projects, or past events.\n"
            f"{recalled}\n"
        )

    def _handle_tool_calls(self, response, tool_activity: list[dict]) -> list[dict]:
        """Execute tool calls from Claude's response, appending to tool_activity."""
        tool_results = []

        for block in response.content:
            # After sanitisation in the streaming path, blocks are plain
            # dicts; the non-streaming path has SDK objects. Read fields
            # uniformly to handle both shapes.
            if self._block_field(block, "type") != "tool_use":
                continue

            tool_name = self._block_field(block, "name")
            tool_input = self._block_field(block, "input")
            logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])

            skill = self.skill_loader.get_skill(tool_name)
            if skill:
                result = self.container_manager.execute_skill(skill, tool_input)
                result = self._extract_and_save_remember(result)
            else:
                result = f"Unknown tool: {tool_name}"

            tool_activity.append({
                "name": tool_name,
                "input": tool_input,
                "result": result,
            })

            logger.info("Tool result: %s", result[:200])
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": self._block_field(block, "id"),
                    "content": result,
                }
            )

        return tool_results

    @staticmethod
    def _block_field(block, name):
        """Read a content-block field whether the block is an SDK object or a dict."""
        if isinstance(block, dict):
            return block.get(name)
        return getattr(block, name, None)

    def _extract_and_save_remember(self, result: str) -> str:
        """Strip ## remember: blocks from skill output and file them to the memory vault."""
        if not self.memory_provider or "## remember:" not in result.lower():
            return result

        cleaned = result
        for match in _REMEMBER_RE.finditer(result):
            topic = match.group(1).strip()
            content = match.group(2).strip()
            if topic and content:
                filename = self.memory_provider.save_note(topic, content)
                if filename:
                    logger.info("Skill filed memory: %s", filename)
            cleaned = cleaned.replace(match.group(0), "")

        return cleaned.strip() or "Skill completed with no output"

    @staticmethod
    def _sanitize_block(block):
        """Strip SDK-internal helper fields from a content block.

        Anthropic's streaming SDK attaches `parsed_output` to text blocks
        (and would for tool_use blocks too, in some configurations). The
        API rejects those fields with HTTP 400 'Extra inputs are not
        permitted' when we echo the content back on the next turn.
        Convert to a plain dict via model_dump and keep only API-accepted
        fields per block type.
        """
        if hasattr(block, "model_dump"):
            data = block.model_dump(exclude_none=True)
        elif isinstance(block, dict):
            data = dict(block)
        else:
            return block

        block_type = data.get("type")
        allowed = {
            "text": {"type", "text", "citations"},
            "tool_use": {"type", "id", "name", "input"},
            "thinking": {"type", "thinking", "signature"},
            "redacted_thinking": {"type", "data"},
        }
        keep = allowed.get(block_type)
        if keep is None:
            return data
        return {k: v for k, v in data.items() if k in keep}

    def _extract_text(self, response) -> str:
        """Extract text content from Claude's response.

        Handles both SDK content-block objects (non-streaming path) and
        sanitised dicts (streaming path)."""
        parts = []
        for block in response.content:
            if self._block_field(block, "type") == "text":
                text = self._block_field(block, "text")
                if text:
                    parts.append(text)
        return " ".join(parts)
