// Loaded by `copilot --experimental --plugin-dir <this plugin>` (see
// src/llm/copilot_cli.py): replaces Copilot's built-in system prompt with the
// one CopilotCLI passes in EPUB_TRANSLATOR_SYSTEM_PROMPT.
import { joinSession } from "@microsoft/copilot-sdk/extension";

await joinSession({
  systemMessage: { mode: "replace", content: process.env.EPUB_TRANSLATOR_SYSTEM_PROMPT },
});
