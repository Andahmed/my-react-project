# ======================================================================
# مرحلة 1 (محتاجة إنترنت وقت التسطيب بس): تجميع React جوه ملفات محلية
#   - شاشة الإدارة الجديدة (admin)
#   - شاشة الكاشير: بنجمّع React/ReactDOM/lucide جوه cashier.js بدل ما يتحمّلوا من esm.sh
# بعد التسطيب مفيش أي طلب للإنترنت.
# ======================================================================
FROM node:20-alpine AS fe-build
WORKDIR /fe
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build --config vite.admin.config.ts
COPY app/react-dist/assets/cashier.js ./cashier-src.js
RUN mkdir -p /out && npx esbuild cashier-src.js --bundle --format=esm --minify --target=es2020 \
      --define:process.env.NODE_ENV='"production"' --outfile=/out/cashier.js

# ======================================================================
# مرحلة 2: السيرفر (Python)
# ======================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    ISTAKOZA_DATA=/data \
    TZ=Africa/Cairo

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/server.py app/pos_istakoza.html /app/
COPY app/react-dist /app/react-dist
# الكاشير: صفحة من غير importmap + cashier.js مجمّع فيه React
COPY app/react-dist/index.offline.html /app/react-dist/index.html
COPY --from=fe-build /out/cashier.js /app/react-dist/assets/cashier.js
# شاشة الإدارة الجديدة
COPY --from=fe-build /fe/dist-admin/admin.html /app/react-dist/admin.html
COPY --from=fe-build /fe/dist-admin/assets/ /app/react-dist/assets/

# فحص وقت البناء: لو لسه فيه أي اعتماد على esm.sh أو أي رابط خارجي يفشل البناء
RUN ! grep -rIl "esm.sh" /app/react-dist /app/pos_istakoza.html \
    && test "$(wc -c < /app/react-dist/assets/cashier.js)" -gt 80000

VOLUME /data

# المود والبورت بييجوا من docker-compose (ISTAKOZA_MODE / ISTAKOZA_PORT)
CMD ["python", "server.py", "--no-browser"]
