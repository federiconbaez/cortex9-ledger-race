# ledger-race

Verificador de **saldos e idempotencia** para APIs de billetera / pagos.
Dispara ráfagas sincronizadas y reintentos sobre los caminos de dinero
(débitos concurrentes, reintentos con la misma `Idempotency-Key`, webhooks
firmados repetidos o falsificados, transferencias que fallan a mitad de camino)
y después verifica **invariantes contables sobre el libro mismo**, con SQL de
solo lectura:

| Invariante | Qué exige |
|---|---|
| `I1_balance_equals_entries` | el saldo de cada cuenta = suma de sus movimientos |
| `I2_no_negative_balance` | ningún saldo < 0 |
| `I3_one_entry_per_idempotency_key` | cada `Idempotency-Key` genera un solo asiento por tipo |
| `I4_one_entry_per_webhook_event` | cada `event_id` de webhook, un solo asiento |
| `I5_transfers_balance_to_zero` | cada transferencia tiene sus dos patas y suma 0 |
| `I6_entry_sign_matches_kind` | créditos > 0, débitos < 0 |

Además chequea expectativas de protocolo (`E1`..`E7`): no aceptar sobregiros,
misma respuesta ante el mismo reintento, rechazar webhooks con firma inválida,
revertir transferencias fallidas, rechazar montos no positivos.

> Estado: **MVP de un día**. Núcleo de la CLI + una billetera de demo con
> 8 bugs sembrados como prueba de valor. Producto completo estimado en ~3
> semanas de una persona. **Úsese solo contra sistemas propios o con
> autorización escrita** (sandbox / instancia de prueba).

## Uso

```sh
# demo autocontenida contra la billetera incluida (sin dependencias externas)
python -m ledgerrace demo                     # versión corregida -> PASS
python -m ledgerrace demo --bug idem_race     # un bug sembrado   -> FAIL

# contra una API propia que siga el contrato de abajo
WEBHOOK_SECRET=... python -m ledgerrace check --url http://127.0.0.1:8080 --db ledger.sqlite
```

Códigos de salida: `0` PASS, `1` FAIL, `3` ERROR (hubo pedidos sin
respuesta: nunca se informa PASS en ese caso), `2` uso.

Contrato HTTP que habla el MVP: `POST /accounts {"id"}`, `POST /credit` y
`POST /debit {"account","amount"}`, `POST /transfer {"from","to","amount"}`
(todos con `Idempotency-Key`), `POST /webhook` con `X-Signature` =
HMAC-SHA256 hex del cuerpo, `GET /accounts/<id>`. Esquema del libro:
`accounts(id, balance)`, `entries(account, amount, kind, idem_key, event_id,
transfer_id)` (ver `demo/wallet.py`).

## Qué está probado

Medido en esta máquina (Linux x86_64, 8 CPU, Python 3.13) el 2026-10-27:

```sh
python -m lab.measure
# bugs detected in every run: 8/8 (in at least one run: 8/8); false positives on fixed wallet: 0/20; criterion met: True
```

- Criterio fijado antes de correr: detectar >= 7/8 bugs sembrados y 0 falsos
  positivos en la versión corregida a lo largo de 20 corridas.
  **Resultado: 8/8 bugs detectados en cada una de 5 corridas por bug, 0/20
  falsos positivos.** Corrido dos veces seguidas con el mismo resultado.
- Un bug cuenta como detectado solo si aparece **el hallazgo que lo nombra**
  (tabla `EXPECTED` en `lab/measure.py`), no cualquier FAIL.
- Los 8 bugs (`demo/wallet.py`): `debit_no_lock`, `idem_ignored`,
  `idem_race`, `webhook_replay`, `webhook_unsigned`, `transfer_partial`,
  `balance_drift`, `negative_amount`.
- Tests: `python -m tests.run` corre 20 tests (cada invariante dispara y el
  libro sano pasa, apertura solo lectura, billetera corregida, ráfaga
  sincronizada, API caída -> no PASS, laboratorio corto). Un test salteado
  cuenta como falla.

## Qué NO está probado (límites honestos)

- **Las ventanas de carrera de los bugs sembrados están ensanchadas** con un
  `sleep` de 5 ms. Sin ese ensanche (`python -m lab.measure --window 0
  --clean-runs 0`), `idem_race` se detectó en 4/5 corridas en un intento y en
  3/5 en otro; los otros 7 bugs, 5/5 en ambos. Es decir: sin ensanche el
  criterio (>= 7/8 detectados siempre) se sigue cumpliendo, pero la carrera de
  idempotencia se escapa a veces. Las carreras reales suelen ser más angostas.
- La billetera de demo y los escenarios los escribió el mismo autor, sabiendo
  qué bugs había; 8/8 no prueba cobertura sobre APIs reales.
- El chequeo necesita **leer el libro** (SQLite, solo lectura). Postgres /
  MySQL y el mapeo de esquemas propios no están implementados.
- El contrato HTTP es fijo; no hay adaptadores para APIs de terceros.
- No se probó contra ninguna pasarela ni sistema real.

## Fuera de alcance

Probar sistemas de terceros sin autorización; pasarelas reales en
producción; casinos / apuestas como mercado objetivo.

## Fuentes que se leyeron

- Demanda: pedido abierto en Freelancer "Gaming Platform Security & API
  Audit", INR 1.500-12.500 precio fijo, 13 ofertas; la vista previa habla de
  una instancia de prueba autorizada (leído por la API pública el
  2026-10-27):
  https://www.freelancer.com/projects/security-auditing/Gaming-Platform-Security-API-Audit

## Licencia

Apache-2.0. Ver `LICENSE`.
