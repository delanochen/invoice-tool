// Display instants in one zone without changing stored capture dates or metadata.
window.formatPhotoTime = function(value, zone) {
  if (!value) return '—';
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(value)) return value;
  const date=new Date(value); if(Number.isNaN(date.getTime())) return value;
  const options={year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZoneName:'shortOffset'};
  try { return new Intl.DateTimeFormat('sv-SE',{...options,timeZone:zone||undefined}).format(date); }
  catch { return new Intl.DateTimeFormat('sv-SE',options).format(date); }
};
document.addEventListener('DOMContentLoaded',()=>document.querySelectorAll('[data-photo-time]').forEach(el=>{el.textContent=window.formatPhotoTime(el.dataset.photoTime,el.dataset.photoZone);el.title=el.dataset.photoTime;}));
