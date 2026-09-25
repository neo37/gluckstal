"""Create a superuser from ADMIN_USERNAME / ADMIN_PASSWORD unless that user already exists."""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create a superuser from environment variables (once)"

    def handle(self, **kw):
        username, password = os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_PASSWORD")
        if not (username and password):
            self.stdout.write("ADMIN_USERNAME/ADMIN_PASSWORD are not set — skipping")
            return
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"user {username} already exists")
            return
        User.objects.create_superuser(username=username, password=password, email="")
        self.stdout.write(self.style.SUCCESS(f"superuser {username} created"))
