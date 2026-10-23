"""Account fields shared by enrollment and booking team management."""
from django import forms
from django.contrib.auth import get_user_model


class TeamEmailForm(forms.ModelForm):
    email = forms.EmailField(required=True, max_length=254)

    class Meta:
        model = get_user_model()
        fields = ["email"]
