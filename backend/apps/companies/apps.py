from django.apps import AppConfig


class CompaniesConfig(AppConfig):
    name = "apps.companies"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out

        def _on_login(sender, request, user, **kwargs):
            if request is None or user is None or not getattr(user, "is_organization", False):
                return
            if not request.session.session_key:
                request.session.save()
            from apps.companies.services import register_organization_session

            register_organization_session(user, request.session.session_key)

        def _on_logout(sender, request, user, **kwargs):
            if request is not None and getattr(request, "session", None):
                skey = request.session.session_key
                if skey:
                    from apps.companies.services import remove_organization_session

                    remove_organization_session(skey)

        user_logged_in.connect(_on_login, dispatch_uid="organizations_org_session_login")
        user_logged_out.connect(_on_logout, dispatch_uid="organizations_org_session_logout")

