def setup_django():
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            SECRET_KEY="asset-audit-tests",
            INSTALLED_APPS=[
                "django.contrib.auth",
                "django.contrib.contenttypes",
                "django.contrib.sessions",
                "django.contrib.messages",
                "asset_audit",
            ],
            LOGIN_TOKEN_SCOPES=[
                "publicData",
                "esi-assets.read_assets.v1",
                "esi-characters.read_corporation_roles.v1",
                "esi-assets.read_corporation_assets.v1",
                "esi-contracts.read_character_contracts.v1",
                "esi-contracts.read_corporation_contracts.v1",
                "esi-markets.read_character_orders.v1",
                "esi-markets.read_corporation_orders.v1",
                "esi-universe.read_structures.v1",
            ],
            ASSET_AUDIT_POLL_REFERENCE_DATA_ON_STARTUP=False,
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            ROOT_URLCONF="asset_audit.urls",
            ALLOWED_HOSTS=["testserver"],
            MIDDLEWARE=[
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ],
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "APP_DIRS": False,
                    "OPTIONS": {
                        "loaders": [
                            (
                                "django.template.loaders.locmem.Loader",
                                {
                                    "allianceauth/base-bs5.html": (
                                        "{% block page_title %}{% endblock %}"
                                        "{% block content %}{% endblock %}"
                                        "{% block extra_script %}{% endblock %}"
                                    )
                                },
                            ),
                            "django.template.loaders.app_directories.Loader",
                        ],
                        "context_processors": [
                            "django.template.context_processors.request",
                            "django.contrib.auth.context_processors.auth",
                            "django.contrib.messages.context_processors.messages",
                        ],
                    },
                }
            ],
            USE_TZ=True,
        )

    import django
    from django.core.management import call_command

    if not django.apps.apps.ready:
        django.setup()
    call_command("migrate", verbosity=0, interactive=False)