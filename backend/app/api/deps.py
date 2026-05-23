from typing import AsyncGenerator
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import verify_token
from app.models.user import User

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"/api/v1/auth/login"
)


async def get_current_user(
    db: AsyncSession = Depends(get_db), 
    token: str = Depends(reusable_oauth2)
) -> User:
    """Verifies JWT token credentials and retrieves the authenticated user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Verify and extract subject from token
    user_id_str = verify_token(token)
    if user_id_str is None:
        raise credentials_exception
        
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise credentials_exception

    # Query the user
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="User not found"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Inactive user"
        )
        
    return user


async def get_current_active_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    """Verifies the authenticated user is an administrator."""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="The user does not have enough privileges"
        )
    return current_user
