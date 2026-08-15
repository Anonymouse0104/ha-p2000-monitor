# P2000 Monitor v0.4.4

## Fixes

- Reworked P2KFlex message extraction so HTML/JavaScript attributes such as `onmouseover`, `onmouseout`, `onclick` and `href` cannot leak into the P2000 message text.
- Keeps the existing P2KFlex time/date handling and stable message-ID protection.
- Keeps the regional incident-history architecture based on the central P2000 feed.

## UI

- Added an integration logo suitable for HACS/repository presentation.
- Added project documentation in the README.

## Upgrade

Install v0.4.4 through HACS and restart Home Assistant after the update.
