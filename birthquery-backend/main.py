# the very basics
import os
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Depends, Query

# middlewares
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# models, bases and db session
from sqlalchemy import text
from sqlalchemy.orm import Session
from models import Base, Users, Queries, QueryComments
from bases import UserBase, QueryBase, QueryCommentBase

# utils, cryptography and others
import uuid
import jwt
import bcrypt
import tracemalloc
from utils import logger
from dotenv import load_dotenv
from datetime import datetime, timedelta

# google cloud and db
from google.cloud import bigquery
from google.oauth2 import service_account
from config.db import engine, SessionLocal

"""
Let's start our FastAPI app :-)
"""
app = FastAPI()



"""
# Docs and API
"""
tracemalloc.start()
app = FastAPI(
    title='Birth Query API',
    description='Birth Query Network for the BigQuery data of births from Google Cloud',
    version='0.1.0'
)

"""
Some util functions, they remain here for
a limited period of time, hopefully
"""

"""
Decode authorization, it receives input string
of the string form "Bearer ey..." with the token
following after the Bearer, it exports to all crud
endpoints, it uses pyjwt for the decoding and exceptions
"""
def decode_authorization(authorization: str) -> dict:  
    token = authorization.replace("Bearer ", "")
    try:
        decoded_token = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

    return decoded_token

"""
Start the DB top-level session
this is for the dependency injection
in the following 'Depends' of our API
"""
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


"""
Let's import those secrety things
by using python-dotenv and os
"""
load_dotenv()

URL_DATABASE = os.getenv('URL_DATABASE')
SECRET_KEY = os.getenv('SECRET_KEY')
GOOGLE_KEY = os.getenv('GOOGLE_KEY')
ADMIN_USER = os.getenv('ADMIN_USER')
ADMIN_SECRET = os.getenv('ADMIN_SECRET')


"""
Middleware config
"""
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1'])

app.add_middleware(
    CORSMiddleware,
    allow_origins=['localhost', '127.0.0.1'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get("/")
def hello_fastapi():
    return {"message": "Hello FastAPI"}



"""
Signup endpoint accepts user object (username, password)
Encrypts password data and returns successful response
or exceptions, data validated automatically by pydantic
parametrized query from sqlalchemy avoids sql injection
"""
@app.post("/signup")
async def create_user(user: UserBase, db: Session = Depends(get_db)):
    existing_user = db.query(Users).filter(Users.username == user.username).first()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    pwhash = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt())
    password_hash = pwhash.decode('utf8')
    user_uuid = uuid.uuid4()

    new_user = Users(uuid=user_uuid, username=user.username, password=password_hash)
    db.add(new_user)
    db.commit()
    return {"message": f"User '{user.username}' registered successfully"}


"""
Login endpoint accepts user object (username, password)
Decrypts password data and returns successful response
access token with expiration time encoded by jwt
or exceptions, for non existing user or wrong pswd
"""
@app.post("/login")
async def login_user(user: UserBase, db: Session = Depends(get_db)):
    db_user = db.query(Users).filter(Users.username == user.username).first()

    if db_user is None or not bcrypt.checkpw(user.password.encode('utf-8'), db_user.password.encode('utf-8')):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    access_token_expires = timedelta(minutes=90)
    access_token = jwt.encode({"sub": user.username, "exp": datetime.utcnow() + access_token_expires}, SECRET_KEY, algorithm='HS256')

    if (db_user.username == ADMIN_USER):
        return {"access_token": access_token, "token_type": "bearer", "username": db_user.username, "admin_secret": ADMIN_SECRET, "user_uuid": db_user.uuid }
    else:
        return {"access_token": access_token, "token_type": "bearer", "username": db_user.username, "user_uuid": db_user.uuid }


"""
Retrieve users endpoint, it requires authorization
str and decode_authorization function to work
it returns a JSON with all users excluding pswds
"""
@app.get("/users")
async def retrieve_users(authorization: str = None, db: Session = Depends(get_db)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    decoded_token = decode_authorization(authorization)
    if decoded_token:
        db_query = text("""
            SELECT u.uuid, u.username,
                CASE
                    WHEN COUNT(q.id) = 0 THEN '[]'::jsonb
                    ELSE jsonb_agg(jsonb_build_object('id', q.id, 'name', q.name, 'query_url', q.query_url, 'user_comment', q.user_comment, 'visible', q.visible, 'created_at', q.created_at))
                END AS queries
            FROM users u
            LEFT JOIN queries q ON u.uuid = q.user_uuid
            GROUP BY u.uuid, u.username;
        """)
        db_users = db.execute(db_query).fetchall()
        users = [{
            "uuid": query.uuid, 
            "user_username": query.username,
            "queries": query.queries
            } for query in db_users]
        return users
    raise HTTPException(status_code=401, detail="Not authorized")

"""
get user
"""
@app.get("/users/{user_uuid}")
async def retrieve_user(authorization: str = None, db: Session = Depends(get_db), user_uuid: str = None):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    if not user_uuid:
        raise HTTPException(status_code=400, detail="No user selected to get")
    
    decoded_token = decode_authorization(authorization)
    if decoded_token:
        db_query = text("""
            SELECT u.uuid, u.username,
                CASE
                    WHEN COUNT(q.id) = 0 THEN '[]'::jsonb
                    ELSE jsonb_agg(jsonb_build_object('id', q.id, 'name', q.name, 'query_url', q.query_url, 'user_comment', q.user_comment, 'visible', q.visible, 'created_at', q.created_at))
                END AS queries
            FROM users u
            LEFT JOIN queries q ON u.uuid = q.user_uuid
            WHERE u.uuid = :user_uuid
            GROUP BY u.uuid, u.username;
        """)
        db_user = db.execute(db_query, {"user_uuid": user_uuid}).first()
        if db_user:
            user = {
                "uuid": db_user.uuid,
                "user_username": db_user.username,
                "queries": db_user.queries,
            }
            return user
        return HTTPException(status_code=404, detail="User doesn't exist")
    
    raise HTTPException(status_code=401, detail="Not authorized")


"""
endpoint edit user
"""
@app.patch("/users/{user_uuid}")
async def edit_user(authorization: str = None, admin_secret: str = None, db: Session = Depends(get_db), user_uuid: str = None, existing_password: str = None, new_password: str = None):
    db_user = db.query(Users).filter(Users.uuid == user_uuid).first()
    
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    if not user_uuid:
        raise HTTPException(status_code=400, detail="No user selected to patch")
    
    if not db_user:
        return HTTPException(status_code=404, detail="User doesn't exist")

    if not bcrypt.checkpw(existing_password.encode('utf-8'), db_user.password.encode('utf-8')):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    
    if admin_secret and (admin_secret != ADMIN_SECRET):
        raise HTTPException(status_code=401, detail="Missing custom authorization header")

    decoded_token = decode_authorization(authorization)
    if decoded_token and ((db_user.username == ADMIN_USER and admin_secret == ADMIN_SECRET) or (db_user.username == decoded_token['sub'])):
            pwhash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            password_hash = pwhash.decode('utf8')
            db_user = db.merge(db_user)
            db_user.password = password_hash
            db.commit()
            return {"message": f"User '{db_user.username}' updated successfully"}        
    
    raise HTTPException(status_code=401, detail="Not authorized")


"""
Delete user endpoint, it requires authorization
str and decode_authorization function additionally,
it also requires admin_secret to work
it returns a JSON with all users excluding pswds
"""
@app.delete("/users/{user_uuid}")
async def remove_user(authorization: str = None, admin_secret: str = None, db: Session = Depends(get_db), user_uuid: str = None):
    if not authorization and not admin_secret:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    if admin_secret and (admin_secret != ADMIN_SECRET):
        raise HTTPException(status_code=401, detail="Missing custom authorization header")

    if not user_uuid:
        raise HTTPException(status_code=400, detail="No user selected to delete")

    decoded_token = decode_authorization(authorization)

    if decoded_token:
        db_user = db.query(Users).filter(Users.uuid == user_uuid).first()

        if db_user:
            user_username = db_user.username
            db_delete_queries = text("DELETE FROM queries WHERE user_uuid = :user_uuid;")
            db.execute(db_delete_queries, {"user_uuid": user_uuid})
            db_query = text("DELETE FROM users WHERE uuid = :user_uuid;")
            db.execute(db_query, {"user_uuid": user_uuid})
            db.flush()
            db.commit()
            db.expire(db_user)
            return {"message": f"User '{user_username}' deleted succesfully"}
        
        return HTTPException(status_code=404, detail="User doesn't exist")

    raise HTTPException(status_code=401, detail="Not authorized")



"""
Create new query endpoint
"""
@app.post("/queries")
async def create_query(query: QueryBase, authorization: str = None, admin_secret: str = None, db: Session = Depends(get_db)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    if admin_secret and (admin_secret != ADMIN_SECRET):
            raise HTTPException(status_code=401, detail="Missing custom authorization header")
    
    decoded_token = decode_authorization(authorization)

    if decoded_token:
        user_username = decoded_token['sub']
        db_user = db.query(Users).filter(Users.username == user_username).first()

        if db_user:
            if (db_user.username == ADMIN_USER and admin_secret == ADMIN_SECRET):
                new_query = Queries(
                    user_uuid=db_user.uuid,
                    name=query.name,
                    query_url=query.query_url,
                    user_comment=query.user_comment,
                    primal=True,
                    visible=True,
                    created_at=datetime.utcnow()
                )
                db.add(new_query)
                db.commit()
                return {"message": f"Query '{query.name}' registered successfully"}
            
            elif (db_user.username != ADMIN_USER):
                new_query = Queries(
                    user_uuid=db_user.uuid,
                    name=query.name,
                    query_url=query.query_url,
                    user_comment=query.user_comment,
                    primal=False,
                    visible=True,
                    created_at=datetime.utcnow()
                )
                db.add(new_query)
                db.commit()
                return {"message": f"Query '{query.name}' registered successfully"}
            
            raise HTTPException(status_code=401, detail="Not authorized")
        
        raise HTTPException(status_code=404, detail="Non existing user")
    
    raise HTTPException(status_code=401, detail="Not authorized")


"""
Retrieve queries endpoint, it requires authorization
str and decode_authorization function to work
it returns a JSON with all queries including comments
"""
@app.get("/queries/{query_id}")
async def retrieve_query(authorization: str = None, db: Session = Depends(get_db), query_id: int = None):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    if not query_id:
        raise HTTPException(status_code=400, detail="No query selected to get")
    
    decoded_token = decode_authorization(authorization)
    if decoded_token:
        db_query_query = text("""
                SELECT id, user_uuid, u.username AS user_username, name, query_url, user_comment, visible, created_at 
                FROM queries q
                LEFT JOIN users u ON q.user_uuid = u.uuid
                WHERE q.id = :query_id;
            """)
        
        db_query = db.execute(db_query_query, {"query_id": query_id}).first()
        if db_query:
            db_comments_query = text("""
                SELECT id, user_uuid, u.username AS commented_by, text, like_count 
                FROM query_comments qc
                LEFT JOIN users u ON qc.user_uuid = u.uuid
                WHERE qc.query_id = :query_id;
            """)
            db_comments = db.execute(db_comments_query, {"query_id": query_id}).fetchall()
            query = {
                "id": db_query.id,
                "user_uuid": db_query.user_uuid,
                "user_username": db_query.user_username,
                "name": db_query.name,
                "query_url": db_query.query_url,
                "user_comment": db_query.user_comment,
                "visible": db_query.visible,
                "created_at": db_query.created_at,
                "comments": [
                {
                    "id": comment.id,
                    "user_uuid": comment.user_uuid,
                    "commented_by": comment.commented_by,
                    "text": comment.text,
                    "like_count": comment.like_count,
                }
                for comment in db_comments
            ],
            }
            return query
        raise HTTPException(status_code=404, detail="Query doesn't exist")
    raise HTTPException(status_code=401, detail="Not authorized")

"""
Retrieve queries endpoint, it requires authorization
str and decode_authorization function to work
it returns a JSON with all queries including comments
"""
@app.get("/queries")
async def retrieve_queries(authorization: str = None, db: Session = Depends(get_db)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    decoded_token = decode_authorization(authorization)
    if decoded_token:
        db_query = text("""
            SELECT q.id, q.user_uuid, u.username AS user_username, q.name, q.query_url, q.user_comment, q.visible, q.created_at,
                CASE
                    WHEN COUNT(qc.id) = 0 THEN '[]'::jsonb
                    ELSE jsonb_agg(jsonb_build_object('id', qc.id, 'text', qc.text, 'like_count', qc.like_count, 'commented_by', cu.username))
                END AS comments            
            FROM queries q
            LEFT JOIN query_comments qc ON q.id = qc.query_id
            LEFT JOIN users u ON q.user_uuid = u.uuid
            LEFT JOIN users cu ON qc.user_uuid = cu.uuid
            GROUP BY q.id, q.user_uuid, u.username, q.name, q.query_url, q.user_comment, q.visible, q.created_at, cu.username;
        """)
        db_queries = db.execute(db_query).fetchall()
        queries = [{
            "id": query.id, 
            "user_uuid": query.user_uuid, 
            "user_username": query.user_username,
            "name": query.name, 
            "query_url": query.query_url, 
            "user_comment": query.user_comment,
            "visible": query.visible, 
            "created_at": query.created_at,
            "comments": query.comments
            } for query in db_queries]
        return queries


"""
Get queries endpoint, it requires authorization
str and decode_authorization function to work
it returns a JSON with all queries including comments
"""
@app.patch("/queries/{query_id}")
async def edit_query(authorization: str = None, db: Session = Depends(get_db)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    #if not query_id:
    #    raise HTTPException(status_code=400, detail="No query selected to edit")
    
    decoded_token = decode_authorization(authorization)
    if decoded_token:
        db_query = text("""
            SELECT q.id, q.user_uuid, u.username AS user_username, q.name, q.query_url, q.user_comment, q.primal, q.visible, q.created_at FROM queries;
            FROM queries q
            LEFT JOIN users u ON q.user_uuid = u.uuid
            GROUP BY q.id, q.user_uuid, u.username, q.name, q.query_url, q.user_comment, q.primal, q.visible, q.created_at;
        """)
        db_queries = db.execute(db_query).fetchall()
        queries = [{
            "id": query.id, 
            "user_uuid": query.user_uuid,
            "user_username": query.user_username,
            "name": query.name, 
            "query_url": query.query_url, 
            "user_comment": query.user_comment, 
            "primal": query.primal, 
            "visible": query.visible, 
            "created_at": query.created_at 
            } for query in db_queries]
        return queries
    

"""
Get queries endpoint, it requires authorization
str and decode_authorization function to work
it returns a JSON with all queries including comments
"""
@app.delete("/queries/{query_id}")
async def remove_query(authorization: str = None, admin_secret: str = None, db: Session = Depends(get_db), query_id: int = None):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    if admin_secret and (admin_secret != ADMIN_SECRET):
        raise HTTPException(status_code=401, detail="Missing custom authorization header")

    if not query_id:
        raise HTTPException(status_code=400, detail="No query selected to delete")
    
    decoded_token = decode_authorization(authorization)
    
    db_query = db.query(Queries).filter(Queries.id == query_id).first()
    
    if decoded_token:       
        user_username = decoded_token['sub']
        db_user = db.query(Users).filter(Users.username == user_username).first()
        if db_query and db_user:
            db_confirm_user = db.query(Users).filter(Users.uuid == db_query.user_uuid).first()
            if ((user_username == ADMIN_USER and admin_secret == ADMIN_SECRET) or (user_username == db_confirm_user.username)):
                query_name = db_query.name
                db_query_comments_delete = text("DELETE FROM query_comments WHERE query_id = :query_id;")
                db.execute(db_query_comments_delete, {"query_id": query_id})
                db_query = text(f"DELETE FROM queries WHERE id = :query_id;")
                db.execute(db_query, {"query_id": query_id})
                db.flush()
                db.commit()
                db.expire(db_user)
                return {"message": f"Query '{query_name}' deleted succesfully"}
            
            raise HTTPException(status_code=401, detail="Not authorized")
        
        raise HTTPException(status_code=404, detail="User or Query do not exist")

    raise HTTPException(status_code=401, detail="Not authorized")


"""
Custom
"""
@app.post("/queries/{query_id}")
async def create_comment(query_comment: QueryCommentBase, request: Request, authorization: str = None, db: Session = Depends(get_db), query_id: int = None):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    if not query_id:
        raise HTTPException(status_code=400, detail="No query selected to comment")
    
    decoded_token = decode_authorization(authorization)
    
    db_query = db.query(Queries).filter(Queries.id == query_id).first()

    if decoded_token:       
        user_username = decoded_token['sub']
        db_user = db.query(Users).filter(Users.username == user_username).first()
        if db_user and db_query:
            user_uuid = db_user.uuid

            new_comment = QueryComments(
                user_uuid=user_uuid,
                query_id=query_id,
                text=query_comment.text,
                like_count=query_comment.like_count
            )

            db.add(new_comment)
            db.commit()
            logger.info(f"IP: {request.client.host}, HTTP method: {request.method}, User id: {user_uuid}")
            return {"message": f"Query comment '{query_comment.text}' registered successfully"}
        
        raise HTTPException(status_code=404, detail="User or Query do not exist")

    raise HTTPException(status_code=401, detail="Not authorized")



@app.delete("/queries/{query_id}/{comment_id}")
async def remove_comment(authorization: str = None, admin_secret: str = None, db: Session = Depends(get_db), query_id: int = None, comment_id: int = None):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    if admin_secret and (admin_secret != ADMIN_SECRET):
        raise HTTPException(status_code=401, detail="Missing custom authorization header")

    if not query_id:
        raise HTTPException(status_code=400, detail="No query selected for comment delete")
    
    if not comment_id:
        raise HTTPException(status_code=400, detail="No comment selected to delete")

    decoded_token = decode_authorization(authorization)
    
    db_query = db.query(Queries).filter(Queries.id == query_id).first()
    
    if decoded_token:       
        user_username = decoded_token['sub']
        db_user = db.query(Users).filter(Users.username == user_username).first()
        if db_query and db_user:
            db_confirm_query = db.query(Users).filter(Users.uuid == db_query.user_uuid).first()
            db_confirm_comment = db.query(QueryComments).filter(QueryComments.id == comment_id).first()
            if ((user_username == ADMIN_USER and admin_secret == ADMIN_SECRET) or (user_username == db_confirm_query.username) or (db_confirm_comment.user_uuid == db_user.uuid)):
                query_name = db_query.name
                db_query = text(f"DELETE FROM query_comments WHERE id = :comment_id;")
                db.execute(db_query, {"comment_id": comment_id})
                db.flush()
                db.commit()
                db.expire(db_user)
                return {"message": f"Query comment '{query_name}' deleted succesfully"}
            
            raise HTTPException(status_code=401, detail="Not authorized")
        
        raise HTTPException(status_code=404, detail="User or Query do not exist")

    raise HTTPException(status_code=401, detail="Not authorized")


"""
Config for our BigQuery with GCP
"""
key_path = os.getcwd() + '/' + GOOGLE_KEY

Base.metadata.create_all(bind=engine)

credentials = service_account.Credentials.from_service_account_file(
    key_path,
    scopes=["https://www.googleapis.com/auth/bigquery"],
)

client = bigquery.Client(
    credentials=credentials,
    project=credentials.project_id
)


"""
Custom endpoint for the county natality by father race table
Can accept several parameters for filtering the data
But it is completely functional even without them
It is neccessary that users are authenticated
"""
@app.get("/big-query")
def big_query(
    father_race_code: str = Query(None),
    min_births: float = Query(None),
    max_births: float = Query(None),
    county_fips: str = Query(None),
    min_mother_age: float = Query(None),
    max_mother_age: float = Query(None),
    min_birth_weight: float = Query(None),
    max_birth_weight: float = Query(None),
    limit: int = Query(None), 
    ):
    
    # Our naive SQL query
    sql_query = """
    SELECT * FROM `bigquery-public-data.sdoh_cdc_wonder_natality.county_natality_by_father_race` WHERE 1=1
    """

    # For Fathers_Single_Race_Code
    if father_race_code:
        sql_query += f" AND Fathers_Single_Race_Code = '{father_race_code}'"

    # For Births
    if min_births:
        sql_query += f" AND Births >= {min_births}"

    if max_births:
        sql_query += f" AND Births <= {max_births}"

    # For County_of_Residence_FIPS
    if county_fips:
        sql_query += f" AND County_of_Residence_FIPS = '{county_fips}'"

    # For Ave_Age_of_Mother
    if min_mother_age:
        sql_query += f" AND Ave_Age_of_Mother >= {min_mother_age}"
    
    if max_mother_age:
        sql_query += f" AND Ave_Age_of_Mother <= {max_mother_age}"

    # For Ave_Birth_Weight_gms
    if min_birth_weight:
        sql_query += f" AND Ave_Birth_Weight_gms >= {min_birth_weight}"
    
    if max_birth_weight:
        sql_query += f" AND Ave_Birth_Weight_gms <= {max_birth_weight}"

    # We better don't burn that thing
    if limit:
        if limit <= 2000:
            sql_query += f" LIMIT {limit}"
        else:
            return { "error": "too datadata" }
    else:
        sql_query += f" LIMIT 2000"

    # Make the actual query
    query_job = client.query(sql_query)
    results = query_job.result()

    # JSONify
    data = []
    for row in results:
        data.append(dict(row.items()))
    
    # Return our JSON
    if not data:
        return {"message": "No results found for the given criteria"}
    else:
        return { "data": data }




from fastapi.testclient import TestClient

user_data={"hey": 0}


client = TestClient(app)
def test_read_main():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"msg": "Hello World"}

def test_login_success():
    response = client.post("/login", json=user_data)
    assert response.status_code == 200
    assert response.json() == {"message": "Login successful"}

def test_login_failure():
    invalid_user_data = {"username": "invaliduser", "password": "invalidpassword"}
    response = client.post("/login", json=invalid_user_data)
    assert response.status_code == 200  # Note: Adjust status code based on your actual logic
    assert response.json() == {"message": "Invalid credentials"}


if __name__ == '__main__':
    uvicorn.run('main:app', reload=True)