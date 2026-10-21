from . import actions, agents, approvals, audit, control, identity, metrics

routers = [agents.router, actions.router, audit.router, control.router, approvals.router, identity.router, metrics.router]
