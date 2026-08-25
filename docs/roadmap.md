# Roadmap — listas del SAT en SAT69

## Cubierto hoy
- **Art. 69** (situación fiscal firme): firmes, exigibles, no localizados, cancelados, condonados, sentencias.
- **Art. 69-B** (EFOS — operaciones simuladas): Presunto, Desvirtuado, Definitivo, Sentencia Favorable.
- **Art. 69-B Bis** (transmisión indebida de pérdidas fiscales): Definitivo, Sentencia Favorable.
  - Fuente: CSV Latin-1 del SAT (archivo `Listado_69_B_Bis_Completo`, se descarga como `.xls` pero es CSV).
  - Activación: setear `SAT_URL_69B_BIS` (env / GitHub Secret). Vacío = se omite (feature-flag).
  - Severidad tope: **MEDIO** — es señal de riesgo fiscal del contribuyente, pero no invalida por sí sola los CFDI de la contraparte (a diferencia del 69-B EFOS).

## En seguimiento — Art. 49 Bis
Nueva "lista negra" por CFDI **presuntamente falsos** (verificación exprés, máx. 24 días hábiles, con suspensión inmediata de CFDI al notificar).

**Primera publicación:** el **10-jul-2026** el SAT publicó los primeros oficios de 49 Bis en el DOF (códigos 5793257 y 5793258) — 3 contribuyentes (Alianza Corporativa Camarence A.C., Asociación Patronal Región Zamora A.C., Confederación de Servidores Públicos...). Campos por oficio: nombre, RFC, fecha de efectos.

**Bloqueo (verificado ago-2026):** siguen siendo **oficios individuales en el DOF, sin archivo consolidado** de datos abiertos. Se confirmó por prueba directa que NO existe `Listado_49_Bis_Completo` en los contenedores Azure `Documents_AGGC` ni `Documents_AGAFF` (donde sí vive el 69-B Bis). Construir un verificador determinista requeriría scrapear el DOF (frágil, mantenimiento perpetuo, hoy 3 registros) → fuera de scope hasta que el SAT consolide un archivo, como hizo con el 69-B Bis.

**Watcher:** `.github/workflows/watch-49bis.yml` (semanal) corre `scripts/watch_49bis.py`, que prueba `Documents_AGGC` (candidato más fuerte, donde vive el 69-B Bis), `Documents_AGAFF` y omawww con el patrón `Listado_49_Bis_Completo.*`. Si aparece, abre un issue automático.

**Receta de integración** (cuando aparezca, mirror del 69-B Bis):
1. `config.py`: `SAT_URL_49BIS` (env-overridable) + `HEADER_ROW_49BIS`.
2. `database.py`: tabla `registros_49bis` + índices + FTS `reg49bis_fts` + triggers + `replace_49bis`.
3. `pipeline.py`: `parse_49bis` + `_import_49bis` + dataset `49bis` (y en `all`).
4. `risk.py`: mapeo de severidad en `evaluar()` (nuevo parámetro, con default para back-compat).
5. tools (`verificar_rfc`/`buscar_nombre`/`estado_datos`) + `turso.py` (`_TABLES` + `_SCHEMA_STMTS`) + tests con fixture real.
6. Landing: mover 49 Bis de "próximamente" a cobertura.

## Otras señales candidatas (no priorizadas)
- **CSD sin efectos** (certificados de sello digital cancelados) — el SAT sí lo publica (`CSDsinefectos`). Posible novedad futura, mismo patrón.
