/* Shared catalog controls for workshop participants and party material choices. */
(() => {
  const $ = (selector, root) => root.querySelector(selector);
  const $$ = (selector, root) => [...root.querySelectorAll(selector)];
  function photo(node, design, image) {
    let art = $('.photo-art', node);
    if (!art) { art = document.createElement('span'); art.className = 'photo-art'; node.append(art); }
    art.style.backgroundImage = design?.image_url ? `url("${design.image_url}")` : design?.x ? `url("${image}")` : 'none';
    art.style.backgroundSize = design?.image_url ? 'contain' : '';
    art.style.backgroundPosition = design?.image_url ? 'center' : `${design?.x || '50%'} ${design?.y || '50%'}`;
    art.style.aspectRatio = design?.image_url ? '4 / 5' : design?.y === '50%' ? '1 / 1.08' : design?.y === '100%' ? '1 / 1.15' : '4 / 5';
    node.classList.toggle('no-photo', !design?.image_url && !design?.x);
  }
  function mount(root, designSelect, colorSelect, config, {recipe, available, change}) {
    const designs = config.options[config.design_group], colors = config.options[config.color_group];
    const makeButton = (label, value, className, select) => {
      const node = document.createElement('button');
      node.type = 'button'; node.textContent = label; node.className = className; node.dataset.value = value;
      node.addEventListener('click', () => { select.value = value; change(); });
      return node;
    };
    const update = () => {
      const option = designs.find(d => d.id === designSelect.value);
      const design = config.designs.find(d => d.name === option?.name);
      const color = colors.find(c => c.id === colorSelect.value);
      const exact = config.base_images_only ? null : recipe()?.image_url;
      photo($('.plushie-image', root), exact ? {...design, image_url: exact} : design, config.image);
      $('.tint', root).hidden = Boolean(exact) || Boolean(config.base_images_only);
      $('.tint', root).style.setProperty('--color', color?.swatch || '#fff');
      $('.selection-label', root).textContent = design && color ? `${color.name} ${design.name}` : 'Choose a design and color';
      $$('.design-option', root).forEach(el => el.setAttribute('aria-pressed', String(el.dataset.value === designSelect.value)));
      $$('.color-option', root).forEach(el => {
        el.setAttribute('aria-pressed', String(el.dataset.value === colorSelect.value));
        el.disabled = !available(el.dataset.value);
        el.title = `${colors.find(o => o.id === el.dataset.value)?.name}: ${el.disabled ? 'Unavailable' : 'Available'}`;
      });
      [...colorSelect.options].forEach(o => { o.disabled = Boolean(o.value) && !available(o.value); });
      colorSelect.setCustomValidity(colorSelect.value && !available(colorSelect.value) ? 'This selection is unavailable. Choose another design or color.' : '');
    };
    $('.designs', root).replaceChildren(...designs.map(option => {
      const node = makeButton('', option.id, 'design-option', designSelect);
      const picture = document.createElement('span'); picture.className = 'design-photo';
      photo(picture, config.designs.find(d => d.name === option.name), config.image);
      const label = document.createElement('span'); label.textContent = option.name;
      node.append(picture, label); return node;
    }));
    $('.colors', root).replaceChildren(...colors.map(option => {
      const node = makeButton(option.name, option.id, 'color-option', colorSelect);
      node.style.setProperty('--color', option.swatch); node.setAttribute('aria-label', option.name); return node;
    }));
    designSelect.addEventListener('change', change); colorSelect.addEventListener('change', change);
    update();
    return update;
  }
  window.PontoraCatalogPicker = {photo, mount};
})();
