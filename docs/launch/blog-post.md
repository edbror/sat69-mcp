# Antes de deducir una factura, pregúntale al SAT si el proveedor está en la lista negra

*Un servidor MCP que cruza tu cartera de RFCs contra las listas del 69-B y 69 del CFF y te devuelve un veredicto de riesgo. Para no descubrir un EFOS cuando ya pagaste.*

En México hay un riesgo fiscal que no se ve en el estado de cuenta: pagarle a un proveedor que el SAT tiene marcado como EFOS. Si ese proveedor está en la lista del **69-B como "Definitivo"**, sus CFDI **no producen efectos fiscales** —pierdes la deducción y el acreditamiento del IVA, y quedas expuesto a que te lo determinen en una revisión. La información es pública, pero vive en unos CSV que casi nadie cruza contra su padrón de proveedores antes de facturar.

Así que construimos **SAT69**: un servidor MCP que toma las listas del SAT y las vuelve una capacidad de cumplimiento que tu equipo —o tu agente— puede consultar. Le pasas un RFC o tu cartera completa y regresa un veredicto de riesgo, de `CRÍTICO` a `LIMPIO`.

## Qué revisa

- **Artículo 69-B del CFF (EFOS)** — operaciones simuladas: *Presunto, Desvirtuado, Definitivo, Sentencia Favorable*.
- **Artículo 69 del CFF** — situación fiscal firme: *firmes, exigibles, no localizados, cancelados, condonados*.

El veredicto es directo: `CRÍTICO` (EFOS definitivo), `ALTO` (EFOS presunto), `MEDIO` (69 firme / exigible / no localizado), `BAJO` (desvirtuado / sentencia favorable), `INFORMATIVO` (cancelado / condonado) y `LIMPIO`. El 69-B manda sobre el 69: un proveedor puede estar "firme" y aun así ser el problema real.

## Cómo se usa

- `verificar_rfc` — un RFC, un veredicto.
- `verificar_lote` — hasta 500 RFCs de tu padrón; devuelve **sólo** los hallazgos, ordenados por severidad.
- `buscar_nombre` — cuando tienes la razón social pero no el RFC.
- `estado_datos` — vigencia declarada por el SAT y última importación.

El caso de uso es concreto: correr tu cartera de proveedores antes del alta, antes de pagar y antes de cerrar el mes, para blindar tus deducciones.

## Lo que decimos de frente

**Refleja la última importación, no el segundo exacto.** Los datos se sincronizan con los archivos públicos del SAT (refresco diario). Para una defensa, la constancia oficial del portal del SAT sigue siendo el documento que vale.

**Informa; no es asesoría fiscal.** SAT69 te dice que un RFC aparece en una lista y con qué situación. La decisión —bloquear el pago, pedir aclaración, provisionar el riesgo— es de tu contador o fiscalista. No sustituye ese criterio.

**No estamos afiliados al SAT.** Leemos sus datos abiertos; no somos un canal oficial.

## Es privado, y se conecta a tu flujo

SAT69 no es una web pública: es un servidor MCP que se enchufa a tu agente, a tu ERP o al flujo de cuentas por pagar de tu empresa. Si llevas la contabilidad o el compliance de una empresa mexicana y quieres verlo contra tu propio padrón de proveedores, **escríbenos a ed@watr.mx y agendamos una demo**.
