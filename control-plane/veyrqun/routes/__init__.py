from . import actions, admin, agents, approvals, audit, auth, control, identity, internal, metrics, playground

routers = [auth.router, agents.router, actions.router, audit.router, control.router, approvals.router, admin.router,
           identity.router, internal.router, metrics.router, playground.router]
