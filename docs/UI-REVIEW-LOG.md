# UI review log

Review passes on the hosted app, checked against the ui-ux-pro-max checklist
(accessibility, touch targets, layout at 320/375/430 px, forms and feedback, states).

| Run | Area | Found | Fixed |
|---|---|---|---|
| 0 | Amul poll pacing | Requests went out back to back at the top of the minute | 750 ms gap plus a 0 to 5 s random start; 8 requests now take about 12 s |
| 1 | Sign-up and login (320 and 375 px) | Hints and errors not linked to inputs for screen readers; brand link 24 px tall; no red border on a bad field | `aria-describedby`, `aria-invalid`, `role="alert"` on errors; 44 px brand link with header height unchanged; invalid inputs get the error border. Checked: live hints, busy button blocks double submit, Turnstile space reserved, no overflow at 320 px |
