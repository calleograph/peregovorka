import ImageCropper from "./ImageCropper";

/** Окно обрезки аватарки: квадрат 256×256 (WebP), на экране показывается кругом. Сервер проверяет и перекодирует файл ещё раз. */
export default function AvatarCropper({ file, onCancel, onDone }: { file: File; onCancel: () => void; onDone: (blob: Blob) => Promise<void> }) {
  return <ImageCropper file={file} onCancel={onCancel} onDone={onDone} title="Аватарка" out={256} maxBytes={600 * 1024} shape="circle" hint="Перетащите картинку, чтобы выбрать нужную часть. Сохраняется квадрат; на экране он показывается кругом." />;
}
