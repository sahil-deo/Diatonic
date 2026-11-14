from .app import TelemetryApp


def create(ctx):
    return TelemetryApp(ctx)
