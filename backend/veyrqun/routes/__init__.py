from . import actions, agents, audit, control, metrics

routers = [agents.router, actions.router, audit.router, control.router, metrics.router]
