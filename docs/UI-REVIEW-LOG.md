# UI review log

Review passes on the hosted app, checked against the ui-ux-pro-max checklist
(accessibility, touch targets, layout at 320/375/430 px, forms and feedback, states).

| Run | Area | Found | Fixed |
|---|---|---|---|
| 0 | Amul poll pacing | Requests went out back to back at the top of the minute | 750 ms gap plus a 0 to 5 s random start; 8 requests now take about 12 s |
| 1 | Sign-up and login (320 and 375 px) | Hints and errors not linked to inputs for screen readers; brand link 24 px tall; no red border on a bad field | `aria-describedby`, `aria-invalid`, `role="alert"` on errors; 44 px brand link with header height unchanged; invalid inputs get the error border. Checked: live hints, busy button blocks double submit, Turnstile space reserved, no overflow at 320 px |
| 2 | My alerts and add-alert sheet (375 px) | Blank screen while loading; `/api/me` ran 4 queries in series (480 ms); switch 48x28 and delete 40x40 tap targets; "Up Ncr" region name; pincode text wrapping mid-line; Add button squeezed beside the summary; Save hidden below a long product list; taken products looked selected; no focus return after closing | Loading text; queries in parallel; 56x44 and 44x44 tap areas; "UP NCR"; `201304 · UP NCR` kept on one line; Add button drops to full width on phones; sticky Save/Cancel footer with a live "Add 2 alerts" label; taken rows dimmed; focus goes back to the opener; dialog labelled |
| 3 | Settings (375 px) | "Slack connected" wrapped under its label; two test buttons; a primary "Turn on" button shown while the browser blocks notifications; "How do I..." toggles 20 px tall; Recent messages listed 15 raw rows (3,300 px page) | Channel status stays on the row; one "Send a test to all" in Settings (the onboarding keeps its own); blocked state shows only the fix instructions; 44 px disclosure rows with a rotating marker; 5 recent messages plus "Show all" |
