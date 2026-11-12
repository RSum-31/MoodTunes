from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserPublic(BaseModel):
    id: str
    email: str
    name: str
    save_history: bool


class AuthMeResponse(BaseModel):
    # Guests get {"user": null} with a 200 so the app never logs a 401 on load.
    user: UserPublic | None


class SettingsUpdate(BaseModel):
    save_history: bool
