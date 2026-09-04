(() => {
  'use strict';
  const FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", sans-serif';
  let logoPromise;
  function logo() {
    if (!logoPromise) logoPromise = new Promise(resolve => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => { logoPromise = null; resolve(null); };
      img.src = '/static/logo.svg';
    });
    return logoPromise;
  }
  function rounded(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath(); ctx.moveTo(x + r, y);
    ctx.arcTo(x + width, y, x + width, y + height, r);
    ctx.arcTo(x + width, y + height, x, y + height, r);
    ctx.arcTo(x, y + height, x, y, r);
    ctx.arcTo(x, y, x + width, y, r); ctx.closePath();
  }
  function wrap(ctx, value, width, maxLines) {
    const chars = Array.from(String(value || '').replace(/\s+/g, ' ').trim());
    const lines = []; let line = '';
    for (const char of chars) {
      if (line && ctx.measureText(line + char).width > width) { lines.push(line.trimEnd()); line = char.trimStart(); }
      else line += char;
    }
    if (line) lines.push(line);
    if (lines.length > maxLines) {
      const visible = lines.slice(0, maxLines);
      let last = visible[maxLines - 1];
      while (last && ctx.measureText(last + '…').width > width) last = Array.from(last).slice(0, -1).join('');
      visible[maxLines - 1] = last + '…'; return visible;
    }
    return lines.length ? lines : ['—'];
  }
  function localTime(context) {
    const date = new Date(context.captured_at);
    const parts = new Intl.DateTimeFormat('en-GB', {timeZone:context.timezone_name || 'UTC',
      year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23', timeZoneName:'shortOffset'}).formatToParts(date);
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second} ${values.timeZoneName}`;
  }
  async function draw(ctx, width, height, context) {
    const image = await logo();
    const font = Math.max(12, Math.min(width, height) * .024);
    const cardWidth = width * (width > height ? .50 : .65);
    const pad = font * .85, labelWidth = font * 3.5, lineHeight = font * 1.36;
    const innerWidth = cardWidth - pad * 2, valueWidth = innerWidth - labelWidth;
    const headerFont = font * 1.22, companyFont = font * .90;
    const logoSize = font * 2.12, logoGap = font * .55;
    ctx.save(); ctx.textBaseline = 'top';
    ctx.font = `700 ${headerFont}px ${FONT}`;
    const title = wrap(ctx, context.site_name || context.order_number, innerWidth - logoSize - logoGap, 2);
    const headerHeight = pad * 2 + title.length * headerFont * 1.18 + companyFont * 1.5;
    ctx.font = `600 ${font}px ${FONT}`;
    const rows = [
      ['工单', context.order_number, 1],
      ['设备', context.equipment_number || '无设备编号', 2],
      ['位置', context.position_number || '—', 1],
      ['施工员', context.employee_name, 2],
      ['Time', localTime(context), 2],
      ['Address', context.site_address || context.site_name || '未填写地址', 3]
    ];
    if (context.note) rows.push(['备注', context.note, 2]);
    const renderedRows = rows.map(([label,value,maxLines]) => ({label, lines:wrap(ctx,value,valueWidth,maxLines)}));
    const bodyHeight = renderedRows.reduce((sum,row) => sum + row.lines.length * lineHeight + font * .35, 0);
    const footerFont = font * .72;
    ctx.font = `500 ${footerFont}px ${FONT}`;
    const coords = `${Number(context.latitude).toFixed(5)}, ${Number(context.longitude).toFixed(5)}  ·  ±${Math.round(context.accuracy)}m`;
    const footer = [coords, context.source === 'camera' ? '现场拍摄 · 设备时间' : '系统相机 / 选图 · 本次记录时间'];
    const cardHeight = headerHeight + pad * 2 + bodyHeight + footerFont * 3.2;
    // Landscape, portrait and small images use the same proportions without clipping.
    const scale = Math.min(1, height * .43 / cardHeight);
    const margin = Math.max(6, Math.min(width,height) * .018);
    const x = margin, y = height - margin - cardHeight * scale;
    ctx.translate(x, y); ctx.scale(scale, scale);
    ctx.shadowColor = 'rgba(0,0,0,.18)'; ctx.shadowBlur = font * .6; ctx.shadowOffsetY = font * .16;
    rounded(ctx,0,0,cardWidth,cardHeight,font*.4); ctx.fillStyle='rgba(255,255,255,.91)'; ctx.fill();
    ctx.shadowColor = 'transparent'; ctx.shadowBlur=0; ctx.shadowOffsetY=0;
    ctx.save(); rounded(ctx,0,0,cardWidth,cardHeight,font*.4); ctx.clip();
    const gradient=ctx.createLinearGradient(0,0,cardWidth,headerHeight);
    gradient.addColorStop(0,'rgba(9,83,73,.96)'); gradient.addColorStop(1,'rgba(15,118,110,.93)');
    ctx.fillStyle=gradient; ctx.fillRect(0,0,cardWidth,headerHeight);
    ctx.fillStyle='#8dc63f'; ctx.fillRect(0,headerHeight-font*.13,cardWidth,font*.13);
    ctx.restore();
    if (image) ctx.drawImage(image,pad,pad,logoSize,logoSize);
    const titleX=pad+logoSize+logoGap;
    ctx.font=`700 ${headerFont}px ${FONT}`; ctx.fillStyle='#fff';
    title.forEach((line,i)=>ctx.fillText(line,titleX,pad+i*headerFont*1.18));
    ctx.font=`600 ${companyFont}px ${FONT}`; ctx.fillStyle='#e1f2ec';
    ctx.fillText('Prasinos Power',titleX,pad+title.length*headerFont*1.18+companyFont*.17);
    let rowY=headerHeight+pad;
    renderedRows.forEach(row=>{
      ctx.font=`500 ${font*.86}px ${FONT}`; ctx.fillStyle='#53645e'; ctx.fillText(row.label,pad,rowY+font*.08);
      ctx.font=`600 ${font}px ${FONT}`; ctx.fillStyle='#142e28';
      row.lines.forEach((line,i)=>ctx.fillText(line,pad+labelWidth,rowY+i*lineHeight));
      rowY+=row.lines.length*lineHeight+font*.35;
    });
    ctx.fillStyle='rgba(15,118,110,.18)'; ctx.fillRect(pad,rowY,innerWidth,Math.max(1,font*.035));
    ctx.font=`500 ${footerFont}px ${FONT}`; ctx.fillStyle='#586b63';
    footer.forEach((line,i)=>ctx.fillText(line,pad,rowY+font*.4+i*footerFont*1.4));
    ctx.restore();
    return {x,y,width:cardWidth*scale,height:cardHeight*scale};
  }
  window.PrasinosWatermark = {draw};
})();
