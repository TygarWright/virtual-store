from abc import ABC, abstractmethod
from typing import Generic, TypeVar, List, Optional, Type
from sqlalchemy.exc import SQLAlchemyError
from extensions import db

ModelType = TypeVar("ModelType")


class BaseRepository(ABC, Generic[ModelType]):
    def __init__(self, model: Type[ModelType]):
        self.model = model

    def get(self, id: int) -> Optional[ModelType]:
        """Get a single record by ID."""
        try:
            return self.model.query.get(id)
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e

    def get_all(self) -> List[ModelType]:
        """Get all records."""
        try:
            return self.model.query.all()
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e

    def get_by(self, **filters) -> List[ModelType]:
        """Get records matching filters."""
        try:
            return self.model.query.filter_by(**filters).all()
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e

    def get_first_by(self, **filters) -> Optional[ModelType]:
        """Get first record matching filters."""
        try:
            return self.model.query.filter_by(**filters).first()
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e

    def create(self, **kwargs) -> ModelType:
        """Create a new record."""
        try:
            instance = self.model(**kwargs)
            db.session.add(instance)
            db.session.commit()
            return instance
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e

    def update(self, id: int, **kwargs) -> Optional[ModelType]:
        """Update a record by ID."""
        try:
            instance = self.get(id)
            if instance:
                for key, value in kwargs.items():
                    setattr(instance, key, value)
                db.session.commit()
            return instance
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e

    def delete(self, id: int) -> bool:
        """Delete a record by ID."""
        try:
            instance = self.get(id)
            if instance:
                db.session.delete(instance)
                db.session.commit()
                return True
            return False
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e

    def delete_by(self, **filters) -> int:
        """Delete records matching filters."""
        try:
            deleted = self.model.query.filter_by(**filters).delete()
            db.session.commit()
            return deleted
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e

    def count(self, **filters) -> int:
        """Count records matching filters."""
        try:
            return self.model.query.filter_by(**filters).count()
        except SQLAlchemyError as e:
            db.session.rollback()
            raise e