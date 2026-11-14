from .app import SchedulerApp


def create(ctx):
    return SchedulerApp(ctx)
