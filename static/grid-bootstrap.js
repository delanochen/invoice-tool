// Set before the body is parsed so native tables do not flash before enhancement.
if (!location.pathname.includes('/print')) {
  document.documentElement.classList.add('grids-pending');
  window.setTimeout(()=>document.documentElement.classList.remove('grids-pending'),8000);
}
