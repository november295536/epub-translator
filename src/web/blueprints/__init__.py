"""Flask blueprints for the EPUB translator web interface."""

from .glossary import glossary_bp
from .home import home_bp
from .projects import projects_bp
from .review import review_bp

__all__ = ['glossary_bp', 'home_bp', 'projects_bp', 'review_bp']
