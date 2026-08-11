"""K-VAS (Korea battery status collection, KECO / Ministry of Environment) support
for the local CSMS. See kvas/README.md and the SmartyPluggerIotBoard repo's
`.claude/plans/2026-08-11-kvas-battery-data-to-csms.md` for the design."""

from .handler import VENDOR_ID, handle  # noqa: F401
