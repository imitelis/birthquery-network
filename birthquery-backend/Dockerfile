# Use the official Python image as a base
FROM python:3.10

# Create workdir
RUN mkdir -p /code

# Set the working directory to /code
WORKDIR /code

# Copy the current directory contents into the container at /code
COPY . /code

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Install project dependencies
RUN pip install uvicorn
RUN pip install -r reqs.txt

# Expose a port
EXPOSE 8000

# Run our FastAPI server using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]