declare module 'react-quill' {
    import React from 'react';
    export interface ReactQuillProps {
        theme?: string;
        modules?: any;
        formats?: string[];
        value?: string;
        onChange?: (value: string) => void;
        className?: string;
        placeholder?: string;
    }
    export default class ReactQuill extends React.Component<ReactQuillProps> { }
}
