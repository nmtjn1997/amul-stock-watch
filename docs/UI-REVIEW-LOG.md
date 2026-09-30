# UI review log

Review passes on the hosted app, checked against the ui-ux-pro-max checklist
(accessibility, touch targets, layout at 320/375/430 px, forms and feedback, states).

| Run | Area | Found | Fixed |
|---|---|---|---|
| 0 | Amul poll pacing | Requests went out back to back at the top of the minute | 750 ms gap plus a 0 to 5 s random start; 8 requests now take about 12 s |
