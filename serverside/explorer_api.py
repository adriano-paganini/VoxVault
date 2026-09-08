"""HTTP endpoints for conversation exploration and speaker management."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

import explorer_service


logger = logging.getLogger(__name__)


class ExplorerRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                response = await original(request)
            except explorer_service.ExplorerError as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            except HTTPException as exc:
                response = JSONResponse(
                    {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers,
                )
            except RequestValidationError as exc:
                response = JSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=422)
            except SQLAlchemyError:
                logger.exception("Conversation explorer database operation failed")
                response = JSONResponse(
                    {"detail": "Conversation data is temporarily unavailable. Please retry shortly."},
                    status_code=503,
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


router = APIRouter(prefix="/api/explorer", tags=["explorer"], route_class=ExplorerRoute)


class PersonName(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class NewPerson(PersonName):
    chunkId: int = Field(gt=0)


class PersonAssignment(BaseModel):
    personId: int | None = Field(gt=0)


@router.get("/conversations")
def conversations(
    q: str = Query(default="", max_length=2000),
    mode: Literal["text", "semantic", "conversation"] = "semantic",
    assignment: Literal["all", "assigned", "unassigned"] = "all",
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    min_similarity: float = Query(default=explorer_service.DEFAULT_TEXT_SIMILARITY, ge=-1, le=1, allow_inf_nan=False),
):
    return explorer_service.list_conversations(q, mode, assignment, limit, offset, min_similarity)


@router.get("/conversations/{recording_id}")
def conversation(
    recording_id: int,
    q: str = Query(default="", max_length=2000),
    mode: Literal["text", "semantic", "conversation"] = "semantic",
    assignment: Literal["all", "assigned", "unassigned"] = "all",
    min_similarity: float = Query(default=explorer_service.DEFAULT_TEXT_SIMILARITY, ge=-1, le=1, allow_inf_nan=False),
):
    return explorer_service.get_conversation(recording_id, q, mode, assignment, min_similarity)


@router.get("/chunks")
def chunks(
    q: str = Query(default="", max_length=2000),
    mode: Literal["text", "semantic"] = "text",
    assignment: Literal["all", "assigned", "unassigned"] = "unassigned",
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return explorer_service.search_chunks(q, mode, assignment, limit, offset)


@router.get("/chunks/{chunk_id}")
def chunk(chunk_id: int):
    return explorer_service.get_chunk(chunk_id)


@router.delete("/chunks/{chunk_id}", status_code=204)
def delete_chunk(chunk_id: int):
    explorer_service.delete_chunk(chunk_id)
    return Response(status_code=204)


@router.get("/persons")
def persons(chunk_id: int | None = Query(default=None, gt=0)):
    return explorer_service.list_persons(chunk_id)


@router.post("/persons", status_code=201)
def new_person(body: NewPerson):
    return explorer_service.create_person(body.name, body.chunkId)


@router.get("/persons/{person_id}")
def person(person_id: int):
    return explorer_service.get_person(person_id)


@router.patch("/persons/{person_id}")
def update_person(person_id: int, body: PersonName):
    return explorer_service.rename_person(person_id, body.name)


@router.get("/persons/{person_id}/chunks")
def known_chunks(
    person_id: int,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return explorer_service.person_chunks(person_id, limit, offset)


@router.get("/similar")
def similar(
    chunk_id: int | None = Query(default=None, gt=0),
    person_id: int | None = Query(default=None, gt=0),
    scope: Literal["all", "unassigned", "other"] = "other",
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return explorer_service.similar_chunks(chunk_id, person_id, scope, limit, offset)


@router.put("/chunks/{chunk_id}/person")
def assign(chunk_id: int, body: PersonAssignment):
    return explorer_service.assign_person(chunk_id, body.personId)
