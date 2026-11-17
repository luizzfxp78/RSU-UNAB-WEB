from fastapi import APIRouter

from app.api.v1.endpoints import auth, catalogs, documents, people, projects, system, workflow, project_lifecycle, notifications, roles_authorities, specific_activities, volunteer_workflow, governance, test_advance

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(catalogs.router)
api_router.include_router(people.router)
api_router.include_router(projects.router)
api_router.include_router(documents.router)
api_router.include_router(workflow.router)
api_router.include_router(project_lifecycle.router)
api_router.include_router(specific_activities.router)
api_router.include_router(volunteer_workflow.router)
api_router.include_router(notifications.router)

api_router.include_router(roles_authorities.router)

api_router.include_router(governance.router)

api_router.include_router(test_advance.router)
