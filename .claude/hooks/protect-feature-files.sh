#!/bin/bash
# protect-feature-files.sh
# Blocks direct edits to feature/ files unless a skill is active.
# Used as a PreToolUse hook on Edit|Write tool calls.
#
# Skills must create the marker file before editing feature files:
#   touch /tmp/.claude-kmpilot-skill-active
# And clean up when done:
#   rm -f /tmp/.claude-kmpilot-skill-active

SKILL_MARKER="/tmp/.claude-kmpilot-skill-active"

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only check files under feature/ directories
if [[ "$FILE_PATH" == *"/feature/"* ]] || [[ "$FILE_PATH" == feature/* ]]; then
  # Initialization gate. A repo can end up with the skills but without the core/
  # modules they generate against — no Either, UiState or X-components for the
  # generated code to import. Writing a feature there produces something that
  # cannot compile, so refuse before the first file lands rather than after the
  # build breaks. A project set up by install.sh (either door) has both.
  if [[ ! -f .kmpilot.json && ! -d core/common ]]; then
    echo "Blocked: KMPilot is not initialized in this project (no .kmpilot.json, no core/common), so a generated feature would import Either/UiState/X* from modules that are not here. Run the installer from this repo's root first: curl -fsSL https://github.com/ThisIsSadeghi/KMPilot/releases/latest/download/install.sh | bash -s -- --adopt   (see ADOPTING.md)" >&2
    exit 2
  fi

  # Allow test files (commonTest, desktopTest, androidTest) - test agents write these directly
  if [[ "$FILE_PATH" == *"/commonTest/"* ]] || [[ "$FILE_PATH" == *"/desktopTest/"* ]] || [[ "$FILE_PATH" == *"/androidTest/"* ]] || [[ "$FILE_PATH" == *"/test/"* ]]; then
    exit 0
  fi

  # Allow build.gradle.kts edits (test dependency setup, integration agent)
  if [[ "$FILE_PATH" == *"build.gradle.kts"* ]]; then
    exit 0
  fi

  # Allow edits when a skill is active (marker file exists and is recent)
  if [[ -f "$SKILL_MARKER" ]]; then
    # Staleness check: marker must be less than 2 hours old
    if [[ "$(uname)" == "Darwin" ]]; then
      marker_age=$(( $(date +%s) - $(stat -f %m "$SKILL_MARKER") ))
    else
      marker_age=$(( $(date +%s) - $(stat -c %Y "$SKILL_MARKER") ))
    fi
    if [[ "$marker_age" -lt 7200 ]]; then
      exit 0
    else
      rm -f "$SKILL_MARKER"
    fi
  fi

  # Block direct feature source edits - must use skills
  echo "Blocked: Cannot edit feature source files directly. Use /create-feature or /modify-feature skill first." >&2
  exit 2
fi

exit 0
