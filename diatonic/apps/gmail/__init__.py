from .app import GmailApp


def create(ctx):
    return GmailApp(ctx)
