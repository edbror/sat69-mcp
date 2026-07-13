# landing/ — sat69

Landing estática (Cloudflare Worker + assets), misma casa de marca que
`forge.watr.mx`. En vivo: https://sat69-landing.watr.workers.dev

Redeploy:

```bash
cd landing && CLOUDFLARE_ACCOUNT_ID=f70ea6062f11ed4c504876390978f0d8 npx wrangler deploy
```

El custom domain (p. ej. `sat69.watr.mx`) se conecta vía API/dashboard: el token
de wrangler no tiene permiso de rutas de zona (por eso no va en `wrangler.toml`).
