"""
Reusable email template builders for all modules.
"""

from pydantic import EmailStr

from app.core.config import settings
from app.shared.email.schemas import EmailMessage

# Placeholder copy for in-person UAT until scheduling is integrated.
_UAT_VENUE_PLACEHOLDER = (
    "Main Campus — Admissions Building, Room 101<br>"
    "(123 University Avenue — this is a placeholder address)"
)
_UAT_TIME_PLACEHOLDER = (
    "Saturday, 10:00 AM – 12:00 PM (local time)<br>"
    "(placeholder — you will receive final details from the registrar)"
)


def build_welcome_email(to_email: EmailStr, first_name: str) -> EmailMessage:
    """Build the default welcome email for newly registered users."""
    return EmailMessage(
        to_email=to_email,
        subject="Welcome to Agentic Registrar",
        html_body=(
            f"<p>Hi {first_name},</p>"
            "<p>Your account was created successfully.</p>"
        ),
        text_body=(
            f"Hi {first_name},\n\n"
            "Your account was created successfully."
        ),
    )


def build_portal_credentials_email(
    to_email: EmailStr,
    first_name: str,
    student_id: str,
    temporary_pin: str,
    *,
    portal_url: str | None = None,
) -> EmailMessage:
    """
    Sent when an officer onboards an admitted student into the
    course-management portal. Carries the UGR student ID and a
    single-use 4-digit PIN that the student must replace on first
    login (``User.must_change_password`` is True until they do).
    """
    app_name = settings.APP_NAME
    portal_url = portal_url or settings.PUBLIC_APP_BASE_URL

    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background-color:#f4f6f8;font-family:Georgia,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f4f6f8;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">
        <tr><td style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);padding:28px 32px;text-align:center;">
          <p style="margin:0;color:#cbd5e1;font-size:13px;letter-spacing:0.12em;text-transform:uppercase;">{app_name}</p>
          <h1 style="margin:12px 0 0;color:#ffffff;font-size:22px;font-weight:600;">Welcome to the student portal</h1>
        </td></tr>
        <tr><td style="padding:28px 32px 8px;color:#1e293b;font-size:16px;line-height:1.6;">
          <p style="margin:0 0 16px;">Hi {first_name},</p>
          <p style="margin:0 0 16px;">Your enrollment is complete. You can now log in to the university portal to register for courses, view your timetable, and request academic advice.</p>
          <p style="margin:0 0 12px;font-weight:600;color:#0f172a;">Your student ID</p>
          <p style="margin:0 0 20px;padding:12px 16px;background:#f1f5f9;border-radius:8px;font-family:ui-monospace,monospace;font-size:18px;color:#1d4ed8;">{student_id}</p>
          <p style="margin:0 0 12px;font-weight:600;color:#0f172a;">Temporary PIN</p>
          <p style="margin:0 0 20px;padding:12px 16px;background:#fef3c7;border:1px solid #fde68a;border-radius:8px;font-family:ui-monospace,monospace;font-size:24px;color:#92400e;letter-spacing:0.2em;text-align:center;"><strong>{temporary_pin}</strong></p>
          <p style="margin:0 0 16px;color:#475569;">Use the student ID above as your username and the PIN as your password. <strong>You will be prompted to set a new password on your first login</strong> — the PIN cannot be reused.</p>
        </td></tr>
        <tr><td style="padding:0 32px 32px;text-align:center;">
          <a href="{portal_url}" style="display:inline-block;padding:14px 32px;background:#1d4ed8;color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;border-radius:999px;">Open the portal</a>
        </td></tr>
        <tr><td style="padding:16px 32px 28px;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:12px;line-height:1.5;text-align:center;">
          If you did not expect this message, contact the registrar's office immediately.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    text_body = (
        f"Hi {first_name},\n\n"
        "Your enrollment is complete. You can now log in to the university portal.\n\n"
        f"Student ID: {student_id}\n"
        f"Temporary PIN: {temporary_pin}\n\n"
        "Use the student ID as your username and the PIN as your password. "
        "You will be prompted to set a new password on first login — the PIN cannot be reused.\n\n"
        f"Portal: {portal_url}\n\n"
        f"— {app_name}"
    )

    return EmailMessage(
        to_email=to_email,
        subject=f"Your portal credentials — {student_id}",
        html_body=html_body,
        text_body=text_body,
    )


def build_uat_acceptance_email(
    to_email: EmailStr,
    first_name: str,
    uat_id: str,
    *,
    take_test_session_url: str,
    venue_address_html: str | None = None,
    venue_datetime_html: str | None = None,
) -> EmailMessage:
    """
    Sent when an application is accepted after credential verification
    and moves to UAT_PENDING. Includes venue/time placeholders and a
    link to the simulated UAT completion flow.
    """
    addr = venue_address_html or _UAT_VENUE_PLACEHOLDER
    when = venue_datetime_html or _UAT_TIME_PLACEHOLDER
    app_name = settings.APP_NAME

    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Application accepted — UAT</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6f8;font-family:Georgia,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f4f6f8;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">
          <tr>
            <td style="background:linear-gradient(135deg,#1e3a5f 0%,#2563eb 100%);padding:28px 32px;text-align:center;">
              <p style="margin:0;color:#e0e7ff;font-size:13px;letter-spacing:0.12em;text-transform:uppercase;">{app_name}</p>
              <h1 style="margin:12px 0 0;color:#ffffff;font-size:22px;font-weight:600;line-height:1.3;">Your application has been accepted</h1>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 32px 8px;color:#1e293b;font-size:16px;line-height:1.6;">
              <p style="margin:0 0 16px;">Hi {first_name},</p>
              <p style="margin:0 0 16px;">Congratulations — your undergraduate application has passed credential verification. The next step is the <strong>Undergraduate Admission Test (UAT)</strong>, which is currently conducted <strong>on campus</strong>.</p>
              <p style="margin:0 0 20px;padding:14px 16px;background:#f1f5f9;border-left:4px solid #2563eb;border-radius:0 8px 8px 0;">
                <strong style="color:#0f172a;">Your UAT reference:</strong><br>
                <span style="font-family:ui-monospace,monospace;font-size:15px;color:#1d4ed8;">{uat_id}</span>
              </p>
              <p style="margin:0 0 8px;font-weight:600;color:#0f172a;">Test location (placeholder)</p>
              <p style="margin:0 0 16px;color:#475569;font-size:15px;">{addr}</p>
              <p style="margin:0 0 8px;font-weight:600;color:#0f172a;">Scheduled time (placeholder)</p>
              <p style="margin:0 0 24px;color:#475569;font-size:15px;">{when}</p>
              <p style="margin:0 0 20px;color:#64748b;font-size:14px;">Please bring a valid ID and arrive early. For now, use the button below only when you are ready to <strong>simulate</strong> recording your UAT result in our system (as if you had completed the test on site).</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 32px 32px;text-align:center;">
              <a href="{take_test_session_url}" style="display:inline-block;padding:14px 32px;background:#2563eb;color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;border-radius:999px;">Take UAT (simulation)</a>
              <p style="margin:16px 0 0;font-size:12px;color:#94a3b8;">If the button does not work, copy this link into your browser:<br><span style="word-break:break-all;color:#64748b;">{take_test_session_url}</span></p>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px 28px;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:12px;line-height:1.5;text-align:center;">
              This message was sent because your application status is UAT pending. If you have questions, contact the admissions office.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_body = f"""\
Hi {first_name},

Your undergraduate application has been accepted after credential verification.

Your UAT reference: {uat_id}

Location (placeholder):
{_UAT_VENUE_PLACEHOLDER.replace('<br>', ' ')}

Time (placeholder):
{_UAT_TIME_PLACEHOLDER.replace('<br>', ' ')}

When you are ready to simulate completing the UAT in our system, open this link in your browser:
{take_test_session_url}

— {app_name}
"""

    return EmailMessage(
        to_email=to_email,
        subject=f"Application accepted — next step: UAT ({uat_id})",
        html_body=html_body,
        text_body=text_body,
    )


def build_password_reset_email(
    to_email: EmailStr,
    first_name: str,
    reset_url: str,
    *,
    valid_minutes: int = 30,
) -> EmailMessage:
    """
    Sent when a user requests a password reset via POST /auth/forgot-password.
    Contains a single magic link with a short-lived JWT — clicking it lands
    the user on /reset-password where they pick a new password.
    """
    app_name = settings.APP_NAME

    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background-color:#f4f6f8;font-family:Georgia,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f4f6f8;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">
        <tr><td style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);padding:28px 32px;text-align:center;">
          <p style="margin:0;color:#cbd5e1;font-size:13px;letter-spacing:0.12em;text-transform:uppercase;">{app_name}</p>
          <h1 style="margin:12px 0 0;color:#ffffff;font-size:22px;font-weight:600;">Reset your password</h1>
        </td></tr>
        <tr><td style="padding:28px 32px 8px;color:#1e293b;font-size:16px;line-height:1.6;">
          <p style="margin:0 0 16px;">Hi {first_name},</p>
          <p style="margin:0 0 16px;">We received a request to reset the password on your portal account. Click the button below to choose a new password.</p>
          <p style="margin:0 0 16px;color:#475569;">This link is valid for <strong>{valid_minutes} minutes</strong>. If you didn't request a reset, you can ignore this message — your password will stay the same.</p>
        </td></tr>
        <tr><td style="padding:0 32px 32px;text-align:center;">
          <a href="{reset_url}" style="display:inline-block;padding:14px 32px;background:#1d4ed8;color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;border-radius:999px;">Set a new password</a>
        </td></tr>
        <tr><td style="padding:0 32px 24px;color:#64748b;font-size:12px;line-height:1.5;">
          If the button doesn't work, paste this link into your browser:<br>
          <span style="word-break:break-all;color:#1d4ed8;">{reset_url}</span>
        </td></tr>
        <tr><td style="padding:16px 32px 28px;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:12px;line-height:1.5;text-align:center;">
          For security, never share this link with anyone.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    text_body = (
        f"Hi {first_name},\n\n"
        "We received a request to reset the password on your portal account.\n"
        f"Open the link below within {valid_minutes} minutes to choose a new password:\n\n"
        f"{reset_url}\n\n"
        "If you didn't request a reset, you can ignore this message.\n\n"
        f"— {app_name}"
    )

    return EmailMessage(
        to_email=to_email,
        subject="Reset your password",
        html_body=html_body,
        text_body=text_body,
    )

