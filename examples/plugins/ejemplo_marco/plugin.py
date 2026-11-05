"""Plugin de ejemplo para OpenLaser Studio (API 1).

Muestra cómo un plugin añade una acción que modifica el documento de forma
deshacible. No accede a la máquina: la geometría se ejecuta, si el usuario lo
decide, con la vista previa y la comprobación previa habituales.
"""


def register(api):
    def dibujar_marco():
        ctx = api.context
        if ctx is None:  # sin interfaz (pruebas): no hay documento
            return "sin contexto"
        from openlaser.document.model import make_shape
        from openlaser.geometry import path as gp

        doc = ctx.doc

        def editar():
            capa = doc.layer_for_color("#888888")
            capa.name = "Marco (plugin)"
            capa.output = False  # sólo como guía: no se graba salvo que el usuario lo active
            forma = make_shape(capa.id, gp.rectangle(5, 5, doc.width_mm - 10, doc.height_mm - 10), "Marco")
            doc.shapes.append(forma)
            return forma.id

        sid = ctx.edit("Marco del área (plugin)", editar)
        ctx.select([sid])
        ctx.message("Marco añadido en la capa «Marco (plugin)» (sin salida)")
        return sid

    api.add_action("Dibujar marco del área de trabajo", dibujar_marco)
    api.log.info("Plugin de ejemplo registrado")
