import { Route, Routes } from "react-router-dom";
import { NavBar } from "./NavBar";
import { CatalogPage } from "./pages/CatalogPage";
import { DetailPage } from "./pages/DetailPage";
import { TokensPage } from "./pages/TokensPage";

export function App() {
  return (
    <>
      <NavBar current="discovery" />
      <Routes>
        <Route path="/" element={<CatalogPage />} />
        <Route path="/d/:id" element={<DetailPage />} />
        <Route path="/settings/tokens" element={<TokensPage />} />
      </Routes>
    </>
  );
}
