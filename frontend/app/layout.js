import "./globals.css";
import { Providers } from "./providers";
import AppChrome from "../components/shell/AppChrome";

export const metadata = {
  title: "Clinical Trials Navigator · MongoDB",
  description: "Clinical trial ingestion, patient graph review, and deterministic bidirectional matching — powered by MongoDB Atlas.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <AppChrome>{children}</AppChrome>
        </Providers>
      </body>
    </html>
  );
}
