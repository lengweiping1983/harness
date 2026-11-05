export function mountSecretInput(input, button, copy) {
  const render = (revealed = input.type === 'text') => {
    input.type = revealed ? 'text' : 'password';
    const key = revealed ? 'hide_token' : 'show_token';
    button.dataset.ariaKey = key;
    button.setAttribute('aria-pressed', String(revealed));
    const label = copy(key);
    if (label) {button.setAttribute('aria-label', label); button.title = label;}
    for (const icon of button.querySelectorAll('[data-secret-icon]')) icon.hidden = (icon.dataset.secretIcon === 'show') === revealed;
  };
  button.addEventListener('mousedown', event => event.preventDefault());
  button.addEventListener('click', () => render(input.type !== 'text'));
  button.closest('.secret-input').addEventListener('focusout', event => {
    if (!event.currentTarget.contains(event.relatedTarget)) render(false);
  });
  return render;
}
