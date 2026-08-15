"""P2000 Monitor integration."""

from .p2kflex_fix import apply_patch as _apply_p2kflex_fix

# Apply the P2KFlex compatibility fix before the sensor platform imports and
# instantiates the coordinator.
_apply_p2kflex_fix()
