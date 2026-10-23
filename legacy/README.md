# Archived upstream utilities

These Python scripts and example inputs preserve the original MIT-licensed US calculations and regression tests. They are not installed as skills, are not the Indian workflow, and have no connector configuration. Host-specific packaging and archived skill prompts have been removed. Broker ticket code is an offline log utility; it has no broker transport.

The Indian analysis module reuses the original Wilder RSI in trader-scanner/scan.py. The original-style Indian adapters in `workflow/` also reuse selected scanner, entry, exit, risk, alert and RSS functions with explicit Indian market/data boundaries. Do not run archived US CLIs directly for Indian tasks; use the active `trader-*` entrypoints.
