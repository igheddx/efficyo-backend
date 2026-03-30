from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    login: str = Field(..., min_length=1, max_length=320, description="Email address")
    password: str = Field(..., min_length=1, max_length=512)


class LoginResponse(BaseModel):
    email: str
    display_name: str
