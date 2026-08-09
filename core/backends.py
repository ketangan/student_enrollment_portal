from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Allow login with email address (case-insensitive) in addition to username."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        try:
            user = User.objects.get(email__iexact=username)
        except User.DoesNotExist:
            return None
        except User.MultipleObjectsReturned:
            # Two accounts share the same email — fall through to username lookup.
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
