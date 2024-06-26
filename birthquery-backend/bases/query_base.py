from pydantic import BaseModel, constr

"""
QueryBase as a basis for the query input
pydantic automatically validates data
"""
class QueryBase(BaseModel):
    name: constr(min_length=8, max_length=80)
    query_url: constr(min_length=8, max_length=200)
    user_comment: constr(min_length=8, max_length=200)