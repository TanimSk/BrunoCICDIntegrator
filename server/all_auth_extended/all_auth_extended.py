from django.conf import settings
from allauth.account.adapter import DefaultAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    FRONTEND_URL = settings.FRONTEND_URL

    def send_mail(self, template_prefix, email, context):        
        # Intercept the email confirmation URL and modify it
        if "activate_url" in context:
            context["activate_url"] = (
                f"{self.FRONTEND_URL}/auth/email/confirm/{context['key']}"
            )

        # Intercept the password reset URL and modify it
        if "password_reset_url" in context:
            original_url = context["password_reset_url"]
            # Safely extract the path after /password/reset/confirm/
            path = original_url.split("/password/reset/confirm")[-1]
            context["password_reset_url"] = (
                f"{self.FRONTEND_URL}/auth/password/reset/confirm{path}"
            )

        # Proceed with default mail sending
        super().send_mail(template_prefix, email, context)
