import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import MainPage from './pages/MainPage';
import SearchResultsPage from './pages/SearchResultsPage';
import CompanyDetailPage from './pages/CompanyDetailPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<MainPage />} />
        <Route path="/search" element={<SearchResultsPage />} />
        <Route path="/company/:ticker" element={<CompanyDetailPage />} />
      </Routes>
    </BrowserRouter>
  );
}
