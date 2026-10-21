from . import actions, admin, agents, approvals, audit, auth, control, identity, metrics

routers = [auth.router, agents.router, actions.router, audit.router, control.router, approvals.router, admin.router,
           identity.router, metrics.router]
