"""Flask blueprints for the EPUB translator web interface."""

from .home import home_bp
from .projects import projects_bp
from .review import review_bp

__all__ = ['home_bp', 'projects_bp', 'review_bp']
