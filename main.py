import io
from datetime import date, time
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import (
    Column, Date, Float, ForeignKey, Integer, String, Time, create_engine, event,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
engine = create_engine("sqlite:///mi_base.db", echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


@event.listens_for(engine, "connect")
def _activar_foreign_keys(dbapi_connection, _):
    # SQLite no aplica las claves foráneas salvo que se lo pidamos.
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class Producto(Base):
    __tablename__ = "productos"
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    nombre = Column(String, nullable=False)
    precio = Column(Float, nullable=False)


class Carrito(Base):
    __tablename__ = "carritos"
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    fecha_creacion = Column(Date, nullable=False)
    estado = Column(String, nullable=False)  # "abierto" | "cerrado"

    items = relationship("CarritoProducto", back_populates="carrito", cascade="all, delete-orphan")
    venta = relationship("Venta", back_populates="carrito", uselist=False)


class CarritoProducto(Base):
    __tablename__ = "carrito_producto"
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    id_carrito = Column(Integer, ForeignKey("carritos.id"), nullable=False)
    id_producto = Column(Integer, ForeignKey("productos.id"), nullable=False)
    cantidad = Column(Integer, nullable=False)

    carrito = relationship("Carrito", back_populates="items")
    producto = relationship("Producto")


class Venta(Base):
    __tablename__ = "ventas"
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    fecha = Column(Date, nullable=False)
    hora = Column(Time, nullable=False)
    id_carrito = Column(Integer, ForeignKey("carritos.id"), unique=True, nullable=False)

    carrito = relationship("Carrito", back_populates="venta")


Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
Estado = Literal["abierto", "cerrado"]


class ProductoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str = Field(min_length=1)
    precio: float = Field(ge=0)


class ProductoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: Optional[str] = Field(default=None, min_length=1)
    precio: Optional[float] = Field(default=None, ge=0)


class ProductoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nombre: str
    precio: float


class CarritoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fecha_creacion: date
    estado: Estado


class CarritoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fecha_creacion: Optional[date] = None
    estado: Optional[Estado] = None


class CarritoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    fecha_creacion: date
    estado: str


class ItemAgregar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_producto: int
    cantidad: int = Field(gt=0)


class ItemCarritoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    producto: ProductoOut
    cantidad: int


class CarritoConItems(CarritoOut):
    productos: list[ItemCarritoOut]


class VentaCreate(BaseModel):
    # extra="forbid": si llega `precio_total` en el payload se responde 422.
    model_config = ConfigDict(extra="forbid")
    fecha: date
    hora: time
    id_carrito: int


class VentaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fecha: Optional[date] = None
    hora: Optional[time] = None


class VentaAsignarCarrito(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_carrito: int


class VentaOut(BaseModel):
    id: int
    fecha: date
    hora: time
    id_carrito: int
    precio_total: float

    @field_serializer("hora")
    def _hora_hhmm(self, hora: time) -> str:
        return hora.strftime("%H:%M")


class VentaDetalle(BaseModel):
    id: int
    fecha: date
    hora: time
    precio_total: float
    carrito: CarritoConItems

    @field_serializer("hora")
    def _hora_hhmm(self, hora: time) -> str:
        return hora.strftime("%H:%M")


# ---------------------------------------------------------------------------
# Lógica de negocio / helpers
# ---------------------------------------------------------------------------
def obtener_o_404(db: Session, modelo, id: int, nombre: str):
    obj = db.get(modelo, id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{nombre} no encontrado")
    return obj


def carrito_a_dict(carrito: Carrito) -> dict:
    return {
        "id": carrito.id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "productos": carrito.items,
    }


def calcular_precio_total(carrito: Carrito) -> float:
    return sum(it.producto.precio * it.cantidad for it in carrito.items)


def venta_a_dict(venta: Venta) -> dict:
    return {
        "id": venta.id,
        "fecha": venta.fecha,
        "hora": venta.hora,
        "id_carrito": venta.id_carrito,
        "precio_total": calcular_precio_total(venta.carrito),
    }


def venta_a_detalle(venta: Venta) -> dict:
    return {
        "id": venta.id,
        "fecha": venta.fecha,
        "hora": venta.hora,
        "precio_total": calcular_precio_total(venta.carrito),
        "carrito": carrito_a_dict(venta.carrito),
    }


def validar_carrito_para_venta(db: Session, id_carrito: int, id_venta_actual: Optional[int] = None) -> Carrito:
    """El carrito debe existir, estar cerrado y no pertenecer a otra venta."""
    carrito = obtener_o_404(db, Carrito, id_carrito, "Carrito")
    if carrito.estado != "cerrado":
        raise HTTPException(status_code=400, detail="El carrito debe estar cerrado para asociarlo a una venta")
    if carrito.venta is not None and carrito.venta.id != id_venta_actual:
        raise HTTPException(status_code=409, detail="El carrito ya pertenece a otra venta")
    return carrito


def exigir_carrito_modificable(carrito: Carrito):
    if carrito.venta is not None:
        raise HTTPException(status_code=400, detail="El carrito está asociado a una venta y no puede modificarse")


def confirmar(db: Session):
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se pudo guardar el cambio (datos inválidos o conflicto)")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="API de Gestión de Ventas")


@app.get("/hola-mundo")
def hola_mundo():
    return "Hello world!"


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------
@app.post("/productos", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
def crear_producto(datos: ProductoCreate, db: Session = Depends(get_db)):
    producto = Producto(nombre=datos.nombre, precio=datos.precio)
    db.add(producto)
    confirmar(db)
    db.refresh(producto)
    return producto


@app.get("/productos", response_model=list[ProductoOut])
def listar_productos(db: Session = Depends(get_db)):
    return db.query(Producto).all()


@app.get("/productos/{id}", response_model=ProductoOut)
def obtener_producto(id: int, db: Session = Depends(get_db)):
    return obtener_o_404(db, Producto, id, "Producto")


@app.put("/productos/{id}", response_model=ProductoOut)
def modificar_producto(id: int, datos: ProductoUpdate, db: Session = Depends(get_db)):
    producto = obtener_o_404(db, Producto, id, "Producto")
    for campo, valor in datos.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(producto, campo, valor)
    confirmar(db)
    db.refresh(producto)
    return producto


@app.delete("/productos/{id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_producto(id: int, db: Session = Depends(get_db)):
    producto = obtener_o_404(db, Producto, id, "Producto")
    en_uso = db.query(CarritoProducto).filter(CarritoProducto.id_producto == id).first()
    if en_uso is not None:
        raise HTTPException(status_code=409, detail="El producto está referenciado en al menos un carrito")
    db.delete(producto)
    confirmar(db)


# ---------------------------------------------------------------------------
# Carritos
# ---------------------------------------------------------------------------
@app.post("/carritos", response_model=CarritoOut, status_code=status.HTTP_201_CREATED)
def crear_carrito(datos: CarritoCreate, db: Session = Depends(get_db)):
    carrito = Carrito(fecha_creacion=datos.fecha_creacion, estado=datos.estado)
    db.add(carrito)
    confirmar(db)
    db.refresh(carrito)
    return carrito


@app.get("/carritos", response_model=list[CarritoConItems])
def listar_carritos(db: Session = Depends(get_db)):
    return [carrito_a_dict(c) for c in db.query(Carrito).all()]


@app.get("/carritos/{id}", response_model=CarritoConItems)
def obtener_carrito(id: int, db: Session = Depends(get_db)):
    return carrito_a_dict(obtener_o_404(db, Carrito, id, "Carrito"))


@app.put("/carritos/{id}", response_model=CarritoOut)
def modificar_carrito(id: int, datos: CarritoUpdate, db: Session = Depends(get_db)):
    carrito = obtener_o_404(db, Carrito, id, "Carrito")
    # Un carrito vendido es inmutable (tampoco puede volver de "cerrado" a "abierto").
    exigir_carrito_modificable(carrito)
    for campo, valor in datos.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(carrito, campo, valor)
    confirmar(db)
    db.refresh(carrito)
    return carrito


@app.delete("/carritos/{id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_carrito(id: int, db: Session = Depends(get_db)):
    carrito = obtener_o_404(db, Carrito, id, "Carrito")
    exigir_carrito_modificable(carrito)
    db.delete(carrito)  # los items se borran en cascada
    confirmar(db)


@app.post("/carritos/{id}/productos", response_model=CarritoConItems, status_code=status.HTTP_201_CREATED)
def agregar_producto_a_carrito(id: int, datos: ItemAgregar, db: Session = Depends(get_db)):
    carrito = obtener_o_404(db, Carrito, id, "Carrito")
    exigir_carrito_modificable(carrito)
    if carrito.estado != "abierto":
        raise HTTPException(status_code=400, detail="Solo se pueden agregar productos a un carrito abierto")
    obtener_o_404(db, Producto, datos.id_producto, "Producto")

    item = next((it for it in carrito.items if it.id_producto == datos.id_producto), None)
    if item is None:
        carrito.items.append(CarritoProducto(id_producto=datos.id_producto, cantidad=datos.cantidad))
    else:
        item.cantidad += datos.cantidad
    confirmar(db)
    db.refresh(carrito)
    return carrito_a_dict(carrito)


@app.delete("/carritos/{id}/productos/{id_producto}", status_code=status.HTTP_204_NO_CONTENT)
def quitar_producto_de_carrito(id: int, id_producto: int, db: Session = Depends(get_db)):
    carrito = obtener_o_404(db, Carrito, id, "Carrito")
    exigir_carrito_modificable(carrito)
    if carrito.estado != "abierto":
        raise HTTPException(status_code=400, detail="Solo se pueden quitar productos de un carrito abierto")
    item = next((it for it in carrito.items if it.id_producto == id_producto), None)
    if item is None:
        raise HTTPException(status_code=404, detail="El producto no se encuentra en el carrito")
    carrito.items.remove(item)  # delete-orphan lo elimina de la BD
    confirmar(db)


# ---------------------------------------------------------------------------
# Ventas
# ---------------------------------------------------------------------------
@app.post("/ventas", response_model=VentaOut, status_code=status.HTTP_201_CREATED)
def crear_venta(datos: VentaCreate, db: Session = Depends(get_db)):
    validar_carrito_para_venta(db, datos.id_carrito)
    venta = Venta(fecha=datos.fecha, hora=datos.hora, id_carrito=datos.id_carrito)
    db.add(venta)
    confirmar(db)
    db.refresh(venta)
    return venta_a_dict(venta)


@app.get("/ventas", response_model=list[VentaDetalle])
def listar_ventas(db: Session = Depends(get_db)):
    return [venta_a_detalle(v) for v in db.query(Venta).all()]


# Se declara antes de /ventas/{id} para que "reporte" no se interprete como un id.
@app.get("/ventas/reporte/csv")
def reporte_ventas_csv(db: Session = Depends(get_db)):
    """Exporta las ventas a CSV usando pandas."""
    import pandas as pd

    df = pd.DataFrame([venta_a_dict(v) for v in db.query(Venta).all()],
                      columns=["id", "fecha", "hora", "id_carrito", "precio_total"])
    return Response(
        content=df.to_csv(index=False),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ventas.csv"},
    )


@app.get("/ventas/{id}", response_model=VentaDetalle)
def obtener_venta(id: int, db: Session = Depends(get_db)):
    return venta_a_detalle(obtener_o_404(db, Venta, id, "Venta"))


@app.put("/ventas/{id}", response_model=VentaOut)
def modificar_venta(id: int, datos: VentaUpdate, db: Session = Depends(get_db)):
    venta = obtener_o_404(db, Venta, id, "Venta")
    for campo, valor in datos.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(venta, campo, valor)
    confirmar(db)
    db.refresh(venta)
    return venta_a_dict(venta)


@app.delete("/ventas/{id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_venta(id: int, db: Session = Depends(get_db)):
    venta = obtener_o_404(db, Venta, id, "Venta")
    db.delete(venta)
    confirmar(db)


@app.post("/ventas/{id}/carrito", response_model=VentaDetalle)
def asignar_carrito_a_venta(id: int, datos: VentaAsignarCarrito, db: Session = Depends(get_db)):
    venta = obtener_o_404(db, Venta, id, "Venta")
    validar_carrito_para_venta(db, datos.id_carrito, id_venta_actual=venta.id)
    venta.id_carrito = datos.id_carrito
    confirmar(db)
    db.expire(venta)  # fuerza a recargar la relación con el nuevo carrito
    return venta_a_detalle(venta)


@app.get("/ventas/{id}/comprobante")
def comprobante_venta_pdf(id: int, db: Session = Depends(get_db)):
    """Genera el comprobante de la venta en PDF usando borb."""
    from borb.pdf import Document, Page, Paragraph, PDF, SingleColumnLayout

    venta = obtener_o_404(db, Venta, id, "Venta")

    doc = Document()
    page = Page()
    doc.append_page(page)
    layout = SingleColumnLayout(page)
    layout.append_layout_element(Paragraph(f"Comprobante de venta #{venta.id}"))
    layout.append_layout_element(Paragraph(f"Fecha: {venta.fecha}  Hora: {venta.hora.strftime('%H:%M')}"))
    for it in venta.carrito.items:
        precio = it.producto.precio
        layout.append_layout_element(
            Paragraph(f"{it.producto.nombre}  x{it.cantidad}  @ ${precio:.2f}  =  ${precio * it.cantidad:.2f}")
        )
    layout.append_layout_element(Paragraph(f"TOTAL: ${calcular_precio_total(venta.carrito):.2f}"))

    buffer = io.BytesIO()
    PDF.write(what=doc, where_to=buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=venta_{id}.pdf"},
    )
