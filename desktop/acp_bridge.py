"""Desktop compatibility imports for the shared AGY runtime."""
import subprocess as subprocess

from backend.agy_runtime import (
    AGY_DEFAULT_EXE as AGY_DEFAULT_EXE,
    AgyBridge as AgyBridge,
    parse_mcp_list_output as parse_mcp_list_output,
    ask_agy as ask_agy,
    probe_agy as probe_agy,
    resolve_agy_executable as resolve_agy_executable,
)
