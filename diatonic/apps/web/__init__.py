from .app import WebApp


def create(ctx):
    return WebApp(ctx)
