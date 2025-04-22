
export const highlightReferences = (index: string) => {
    const idx = index.split('-')
    const idx_start = idx[0]
    const idx_end = idx.length > 1 ? idx[1] : idx[0]
    const eStart = document.getElementById(idx_start);
    const eEnd = document.getElementById(idx_end);
    const allParas = eStart?.parentElement?.getElementsByTagName('p')

    if (allParas) {
      for (let i = 0; i < allParas.length; i++) {
        allParas[i].classList.remove('bg-yellow-200');
      }
    }
    
    if (eStart && eEnd) {
      let current :  HTMLElement | null = eStart;
      while (current && current !== eEnd) {
        current.classList.add('bg-yellow-200');
        current = current.nextElementSibling as HTMLElement | null;
      }
      eEnd.classList.add('bg-yellow-200');
      eStart.scrollIntoView({ behavior: 'smooth', block: 'center' });

    }
  }
