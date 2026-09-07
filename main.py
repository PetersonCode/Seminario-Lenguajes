from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from fastapi import FastAPI, HTTPException, status

engine = create_engine('sqlite:///mi_base.db', echo=True, connect_args={"check_same_thread": False})
db = scoped_session(sessionmaker(bind=engine))
Base = declarative_base()
Base.query = db.query_property()

class Persona(Base):
    __tablename__ = 'personas'
    id = Column(Integer, primary_key=True)
    nombre = Column(String)
    email = Column(String, unique=True)
    edad = Column(Integer)

Base.metadata.create_all(bind=engine)
app = FastAPI()

@app.get("/hola-mundo")
def hola_mundo():
    return "Hello world!"

# Obtener todas las personas
@app.get("/personas")
def listar_personas():
    personas = Persona.query.all()
    # Se filtra la metadata interna de SQLAlchemy para que no rompa FastAPI
    return [{k: v for k, v in vars(p).items() if not k.startswith('_')} for p in personas]

# Obtener una persona por ID
@app.get("/personas/{id}")
def obtener_persona(id: int):
    persona = Persona.query.get(id)
    if persona is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    return {k: v for k, v in vars(persona).items() if not k.startswith('_')}

# Crear una nueva persona
@app.post("/personas", status_code=status.HTTP_201_CREATED)
def crear_persona(datos_persona: dict):
    persona_nueva = Persona(
        nombre=datos_persona.get("nombre"),
        email=datos_persona.get("email"),
        edad=datos_persona.get("edad")
    )
    db.add(persona_nueva)
    try:
        db.commit()
        db.refresh(persona_nueva)
    except:
        db.rollback()
        # Esta línea estaba mal indentada en tu código, debe ir dentro del except
        raise HTTPException(status_code=400, detail="Error al crear persona (email duplicado o datos inválidos)")
    
    return {k: v for k, v in vars(persona_nueva).items() if not k.startswith('_')}

# Actualizar una persona existente
@app.put("/personas/{id}")
def modificar_persona(id: int, datos_persona: dict):
    persona = Persona.query.get(id)
    if persona is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    
    persona.nombre = datos_persona.get("nombre") if datos_persona.get("nombre") is not None else persona.nombre
    persona.email = datos_persona.get("email") if datos_persona.get("email") is not None else persona.email
    persona.edad = datos_persona.get("edad") if datos_persona.get("edad") is not None else persona.edad
    
    db.commit()
    return {k: v for k, v in vars(persona).items() if not k.startswith('_')}

# Eliminar una persona
@app.delete("/personas/{id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_persona(id: int):
    persona = Persona.query.get(id)
    if persona is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    db.delete(persona)
    db.commit()


#python -m uvicorn main:app --reload
#con esto pude levantar el servidor y probar los endpoints