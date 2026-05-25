# Resend Live Probe Notes

**Date:** 2026-05-24
**Message ID:** `1cb970fa-56cd-4075-ae61-a9d18eeb0d5c`

## Outcome

The live Resend probe succeeded. Delivery to `operations.blancbleustbarts@gmail.com` worked, and the user confirmed receipt of both the email body and the `.xlsx` attachment.

## Caveats

- **Test Mode Sender**: The email was sent from `onboarding@resend.dev`.
- **Test Mode Restrictions**: When using `onboarding@resend.dev`, Resend only delivers to the email address registered with the Resend account.
- **Production Requirement**: For production use, a verified domain must be added to Resend, and `REPORT_FROM_ADDR` should be overridden to send from that domain.
